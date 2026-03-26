"""MakerStrategy のテスト."""

from unittest.mock import AsyncMock, Mock

import pytest

from standx_mm_bot.client import StandXHTTPClient
from standx_mm_bot.client.exceptions import APIError
from standx_mm_bot.config import Settings
from standx_mm_bot.core.order import OrderManager
from standx_mm_bot.core.risk import RiskManager
from standx_mm_bot.models import Action, Order, OrderStatus, OrderType, Side
from standx_mm_bot.strategy.maker import MakerStrategy


@pytest.fixture
def config() -> Settings:
    """テスト用設定."""
    return Settings(
        standx_private_key="0x" + "a" * 64,
        standx_wallet_address="0x1234567890abcdef",
        standx_chain="bsc",
        symbol="ETH-USD",
        order_size=0.001,
        target_distance_bps=8.0,
        escape_threshold_bps=3.0,
        outer_escape_distance_bps=15.0,
        reposition_threshold_bps=2.0,
        price_move_threshold_bps=5.0,
    )


@pytest.fixture
def mock_http_client() -> Mock:
    """テスト用 HTTP クライアント."""
    client = Mock(spec=StandXHTTPClient)
    client.new_order = AsyncMock()
    client.cancel_order = AsyncMock()
    client.get_position = AsyncMock()
    return client


def _make_order(side: Side, price: float, order_id: str = "test_order_1") -> Order:
    """テスト用注文を作成."""
    return Order(
        id=order_id,
        symbol="ETH-USD",
        side=side,
        price=price,
        size=0.001,
        order_type=OrderType.LIMIT,
        status=OrderStatus.OPEN,
    )


def _make_strategy_with_mocks(config: Settings, mock_http_client: Mock) -> MakerStrategy:
    """モック付きの MakerStrategy を作成."""
    strategy = MakerStrategy(config)
    strategy.http_client = mock_http_client
    strategy.order_manager = OrderManager(mock_http_client, config)
    strategy.risk_manager = RiskManager(mock_http_client, config)
    return strategy


class TestEvaluateOrder:
    """evaluate_order のテスト."""

    def test_escape_buy_approaching(self, config: Settings) -> None:
        """BUY注文: mark_price < order_price & 距離 < 3bps → ESCAPE."""
        strategy = MakerStrategy(config)
        # BUY price=2498.0, mark=2497.5 → distance≈2bps, approaching (mark < order)
        order = _make_order(Side.BUY, 2498.0)
        assert strategy.evaluate_order(order, 2497.5, Side.BUY) == Action.ESCAPE

    def test_escape_sell_approaching(self, config: Settings) -> None:
        """SELL注文: mark_price > order_price & 距離 < 3bps → ESCAPE."""
        strategy = MakerStrategy(config)
        # SELL price=2502.0, mark=2502.5 → distance≈2bps, approaching (mark > order)
        order = _make_order(Side.SELL, 2502.0)
        assert strategy.evaluate_order(order, 2502.5, Side.SELL) == Action.ESCAPE

    def test_escape_not_approaching(self, config: Settings) -> None:
        """BUY注文: mark_price > order_price (離れている) → HOLD."""
        strategy = MakerStrategy(config)
        # BUY price=2498.0, mark=2500.0 → distance=8bps, NOT approaching (mark > order)
        # target=2498.0, drift=0bps → HOLD
        order = _make_order(Side.BUY, 2498.0)
        assert strategy.evaluate_order(order, 2500.0, Side.BUY) == Action.HOLD

    def test_reposition_boundary(self, config: Settings) -> None:
        """距離 > 8bps (10-2) → REPOSITION."""
        strategy = MakerStrategy(config)
        # BUY price=2477.0, mark=2500.0 → distance=92bps >> 8bps
        order = _make_order(Side.BUY, 2477.0)
        assert strategy.evaluate_order(order, 2500.0, Side.BUY) == Action.REPOSITION

    def test_boundary_exact_8bps_is_hold(self, config: Settings) -> None:
        """距離 == 8.0bps → HOLD（> であり >= ではない）."""
        strategy = MakerStrategy(config)
        # BUY: mark=2500, 8bps → price=2500-2500*8/10000=2498.0
        order = _make_order(Side.BUY, 2498.0)
        assert strategy.evaluate_order(order, 2500.0, Side.BUY) == Action.HOLD

    def test_reposition_drift(self, config: Settings) -> None:
        """目標乖離 > 5bps → REPOSITION."""
        strategy = MakerStrategy(config)
        # BUY: mark=2500, target=2498.0, order=2496.5
        # |2496.5-2498.0|/2500*10000 = 6bps > 5bps
        order = _make_order(Side.BUY, 2496.5)
        assert strategy.evaluate_order(order, 2500.0, Side.BUY) == Action.REPOSITION

    def test_hold_normal(self, config: Settings) -> None:
        """正常範囲 → HOLD."""
        strategy = MakerStrategy(config)
        # BUY: mark=2500, target=2498.0, order=2498.0 → drift=0bps
        order = _make_order(Side.BUY, 2498.0)
        assert strategy.evaluate_order(order, 2500.0, Side.BUY) == Action.HOLD


class TestPlaceInitialOrders:
    """_place_initial_orders のテスト."""

    @pytest.mark.asyncio
    async def test_both_sides_placed(self, config: Settings, mock_http_client: Mock) -> None:
        """BUY/SELL 両方の初期注文が発注される."""
        mock_http_client.new_order.return_value = {
            "order_id": "initial_001",
            "status": "OPEN",
        }
        strategy = _make_strategy_with_mocks(config, mock_http_client)
        strategy.mark_price = 2500.0

        await strategy._place_initial_orders()

        assert strategy.bid_order is not None
        assert strategy.ask_order is not None
        assert strategy.bid_order.side == Side.BUY
        assert strategy.ask_order.side == Side.SELL
        # BUY: 2500 - 2500*8/10000 = 2498.0
        assert strategy.bid_order.price == pytest.approx(2498.0)
        # SELL: 2500 + 2500*8/10000 = 2502.0
        assert strategy.ask_order.price == pytest.approx(2502.0)

    @pytest.mark.asyncio
    async def test_one_side_failure_still_places_other(
        self, config: Settings, mock_http_client: Mock
    ) -> None:
        """片側が失敗しても、もう片側は発注される."""
        from standx_mm_bot.client.exceptions import APIError

        call_count = 0

        async def side_effect(**_kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise APIError("BUY order failed")
            return {"order_id": "sell_001", "status": "OPEN"}

        mock_http_client.new_order.side_effect = side_effect
        strategy = _make_strategy_with_mocks(config, mock_http_client)
        strategy.mark_price = 2500.0

        await strategy._place_initial_orders()

        # BUY は失敗、SELL は成功
        assert strategy.bid_order is None
        assert strategy.ask_order is not None


class TestOnPriceUpdate:
    """_on_price_update のテスト."""

    @pytest.mark.asyncio
    async def test_mark_price_updated(self, config: Settings, mock_http_client: Mock) -> None:
        """mark_price が更新される."""
        mock_http_client.new_order.return_value = {
            "order_id": "init_001",
            "status": "OPEN",
        }
        strategy = _make_strategy_with_mocks(config, mock_http_client)

        await strategy._on_price_update({"mark_price": "2500.0"})

        assert strategy.mark_price == 2500.0

    @pytest.mark.asyncio
    async def test_initial_orders_placed_on_first_price(
        self, config: Settings, mock_http_client: Mock
    ) -> None:
        """初回の価格更新で初期注文が発注される."""
        mock_http_client.new_order.return_value = {
            "order_id": "init_001",
            "status": "OPEN",
        }
        strategy = _make_strategy_with_mocks(config, mock_http_client)
        assert strategy._initial_orders_placed is False

        await strategy._on_price_update({"mark_price": "2500.0"})

        assert strategy._initial_orders_placed is True
        assert strategy.bid_order is not None
        assert strategy.ask_order is not None

    @pytest.mark.asyncio
    async def test_replaces_missing_bid_order(
        self, config: Settings, mock_http_client: Mock
    ) -> None:
        """bid_order が None の場合、BUY 注文を再発注する."""
        mock_http_client.new_order.return_value = {
            "order_id": "new_bid_001",
            "status": "OPEN",
        }
        strategy = _make_strategy_with_mocks(config, mock_http_client)
        strategy.mark_price = 2500.0
        strategy._initial_orders_placed = True
        strategy.bid_order = None
        strategy.ask_order = _make_order(Side.SELL, 2502.0, "ask_001")

        await strategy._on_price_update({"mark_price": "2500.0"})

        assert strategy.bid_order is not None
        assert strategy.bid_order.side == Side.BUY

    @pytest.mark.asyncio
    async def test_replaces_missing_ask_order(
        self, config: Settings, mock_http_client: Mock
    ) -> None:
        """ask_order が None の場合、SELL 注文を再発注する."""
        mock_http_client.new_order.return_value = {
            "order_id": "new_ask_001",
            "status": "OPEN",
        }
        strategy = _make_strategy_with_mocks(config, mock_http_client)
        strategy.mark_price = 2500.0
        strategy._initial_orders_placed = True
        strategy.bid_order = _make_order(Side.BUY, 2498.0, "bid_001")
        strategy.ask_order = None

        await strategy._on_price_update({"mark_price": "2500.0"})

        assert strategy.ask_order is not None
        assert strategy.ask_order.side == Side.SELL


class TestOnTrade:
    """_on_trade のテスト."""

    @pytest.mark.asyncio
    async def test_strict_mode_triggers_on_matching_order(
        self, config: Settings, mock_http_client: Mock
    ) -> None:
        """約定検知で厳格モード発動: クローズ + シャットダウン."""
        mock_http_client.get_position.return_value = []
        strategy = _make_strategy_with_mocks(config, mock_http_client)
        strategy.bid_order = _make_order(Side.BUY, 2498.0, "bid_001")
        strategy.ask_order = _make_order(Side.SELL, 2502.0, "ask_001")

        await strategy._on_trade({"order_id": "bid_001"})

        assert strategy._exit_code == 1
        assert strategy._shutdown_event.is_set()

    @pytest.mark.asyncio
    async def test_ignores_unknown_order_id(self, config: Settings, mock_http_client: Mock) -> None:
        """不明な order_id は無視する."""
        strategy = _make_strategy_with_mocks(config, mock_http_client)
        strategy.bid_order = _make_order(Side.BUY, 2498.0, "bid_001")
        strategy.ask_order = _make_order(Side.SELL, 2502.0, "ask_001")

        await strategy._on_trade({"order_id": "unknown_999"})

        assert strategy._exit_code == 0
        assert not strategy._shutdown_event.is_set()


class TestOnOrderUpdate:
    """_on_order_update のテスト."""

    @pytest.mark.asyncio
    async def test_canceled_clears_bid_order(
        self, config: Settings, mock_http_client: Mock
    ) -> None:
        """CANCELED で bid_order が None になる."""
        strategy = _make_strategy_with_mocks(config, mock_http_client)
        strategy.bid_order = _make_order(Side.BUY, 2498.0, "bid_001")
        strategy.ask_order = _make_order(Side.SELL, 2502.0, "ask_001")

        await strategy._on_order_update({"order_id": "bid_001", "status": "CANCELED"})

        assert strategy.bid_order is None
        assert strategy.ask_order is not None

    @pytest.mark.asyncio
    async def test_canceled_clears_ask_order(
        self, config: Settings, mock_http_client: Mock
    ) -> None:
        """CANCELED で ask_order が None になる."""
        strategy = _make_strategy_with_mocks(config, mock_http_client)
        strategy.bid_order = _make_order(Side.BUY, 2498.0, "bid_001")
        strategy.ask_order = _make_order(Side.SELL, 2502.0, "ask_001")

        await strategy._on_order_update({"order_id": "ask_001", "status": "CANCELED"})

        assert strategy.bid_order is not None
        assert strategy.ask_order is None

    @pytest.mark.asyncio
    async def test_filled_updates_status(self, config: Settings, mock_http_client: Mock) -> None:
        """FILLED でステータスが更新される."""
        strategy = _make_strategy_with_mocks(config, mock_http_client)
        strategy.bid_order = _make_order(Side.BUY, 2498.0, "bid_001")

        await strategy._on_order_update({"order_id": "bid_001", "status": "FILLED"})

        assert strategy.bid_order is not None
        assert strategy.bid_order.status == OrderStatus.FILLED

    @pytest.mark.asyncio
    async def test_partially_filled_updates_size_and_status(
        self, config: Settings, mock_http_client: Mock
    ) -> None:
        """PARTIALLY_FILLED でサイズとステータスが更新される."""
        strategy = _make_strategy_with_mocks(config, mock_http_client)
        strategy.bid_order = _make_order(Side.BUY, 2498.0, "bid_001")

        await strategy._on_order_update(
            {
                "order_id": "bid_001",
                "status": "PARTIALLY_FILLED",
                "filled_size": "0.0005",
            }
        )

        assert strategy.bid_order is not None
        assert strategy.bid_order.status == OrderStatus.PARTIALLY_FILLED
        assert strategy.bid_order.filled_size == 0.0005


class TestExecuteAction:
    """_execute_action のテスト."""

    @pytest.mark.asyncio
    async def test_hold_does_nothing(self, config: Settings, mock_http_client: Mock) -> None:
        """HOLD は何もしない."""
        strategy = _make_strategy_with_mocks(config, mock_http_client)
        order = _make_order(Side.BUY, 2498.0)

        result = await strategy._execute_action(Action.HOLD, order, Side.BUY)

        assert result is None
        mock_http_client.new_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_escape_calls_reposition(self, config: Settings, mock_http_client: Mock) -> None:
        """ESCAPE で reposition_order が呼ばれる."""
        mock_http_client.new_order.return_value = {
            "order_id": "escaped_001",
            "status": "OPEN",
        }
        mock_http_client.cancel_order.return_value = {}
        strategy = _make_strategy_with_mocks(config, mock_http_client)
        strategy.mark_price = 2500.0
        order = _make_order(Side.BUY, 2498.0)

        result = await strategy._execute_action(Action.ESCAPE, order, Side.BUY)

        assert result is not None
        assert result.id == "escaped_001"
        # escape price: 2500 - 2500*15/10000 = 2496.25
        mock_http_client.new_order.assert_called()

    @pytest.mark.asyncio
    async def test_escape_failure_falls_back_to_cancel(
        self, config: Settings, mock_http_client: Mock
    ) -> None:
        """ESCAPE 失敗時はキャンセルにフォールバック."""
        from standx_mm_bot.client.exceptions import APIError

        mock_http_client.new_order.side_effect = APIError("reposition failed")
        mock_http_client.cancel_order.return_value = {}
        strategy = _make_strategy_with_mocks(config, mock_http_client)
        strategy.mark_price = 2500.0
        order = _make_order(Side.BUY, 2498.0, "bid_001")

        result = await strategy._execute_action(Action.ESCAPE, order, Side.BUY)

        assert result is None
        # cancel_order が呼ばれたことを確認
        mock_http_client.cancel_order.assert_called()

    @pytest.mark.asyncio
    async def test_reposition_calls_reposition(
        self, config: Settings, mock_http_client: Mock
    ) -> None:
        """REPOSITION で reposition_order が呼ばれる."""
        mock_http_client.new_order.return_value = {
            "order_id": "repo_001",
            "status": "OPEN",
        }
        mock_http_client.cancel_order.return_value = {}
        strategy = _make_strategy_with_mocks(config, mock_http_client)
        strategy.mark_price = 2500.0
        order = _make_order(Side.BUY, 2477.0)

        result = await strategy._execute_action(Action.REPOSITION, order, Side.BUY)

        assert result is not None
        assert result.id == "repo_001"

    @pytest.mark.asyncio
    async def test_reposition_failure_returns_none(
        self, config: Settings, mock_http_client: Mock
    ) -> None:
        """REPOSITION 失敗時は None を返す."""
        from standx_mm_bot.client.exceptions import APIError

        mock_http_client.new_order.side_effect = APIError("reposition failed")
        strategy = _make_strategy_with_mocks(config, mock_http_client)
        strategy.mark_price = 2500.0
        order = _make_order(Side.BUY, 2477.0)

        result = await strategy._execute_action(Action.REPOSITION, order, Side.BUY)

        assert result is None


class TestInFlight:
    """in_flight フラグのテスト."""

    @pytest.mark.asyncio
    async def test_skips_if_bid_in_flight(self, config: Settings, mock_http_client: Mock) -> None:
        """bid_in_flight が True なら処理をスキップ."""
        strategy = _make_strategy_with_mocks(config, mock_http_client)
        strategy.mark_price = 2500.0
        strategy.bid_order = _make_order(Side.BUY, 2498.0, "bid_001")
        strategy._bid_in_flight = True

        await strategy._evaluate_and_act(strategy.bid_order, Side.BUY)

        # in_flight なので API は呼ばれない
        mock_http_client.new_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_if_ask_in_flight(self, config: Settings, mock_http_client: Mock) -> None:
        """ask_in_flight が True なら処理をスキップ."""
        strategy = _make_strategy_with_mocks(config, mock_http_client)
        strategy.mark_price = 2500.0
        strategy.ask_order = _make_order(Side.SELL, 2502.0, "ask_001")
        strategy._ask_in_flight = True

        await strategy._evaluate_and_act(strategy.ask_order, Side.SELL)

        # in_flight なので API は呼ばれない
        mock_http_client.new_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_in_flight_reset_after_action(
        self, config: Settings, mock_http_client: Mock
    ) -> None:
        """処理完了後に in_flight が False にリセットされる."""
        strategy = _make_strategy_with_mocks(config, mock_http_client)
        strategy.mark_price = 2500.0
        # HOLD を返す注文 (距離=8bps, 目標位置)
        strategy.bid_order = _make_order(Side.BUY, 2498.0, "bid_001")

        await strategy._evaluate_and_act(strategy.bid_order, Side.BUY)

        assert strategy._bid_in_flight is False

    @pytest.mark.asyncio
    async def test_in_flight_reset_on_exception(
        self, config: Settings, mock_http_client: Mock
    ) -> None:
        """例外が発生しても in_flight が False にリセットされる."""
        strategy = _make_strategy_with_mocks(config, mock_http_client)
        strategy.mark_price = 2500.0
        # ESCAPE を返す注文 (BUY price=2499.5, mark < order → approaching, distance≈2bps)
        strategy.bid_order = _make_order(Side.BUY, 2499.5, "bid_001")

        # reposition が例外を投げる → escape の cancel fallback も例外
        from standx_mm_bot.client.exceptions import APIError

        mock_http_client.new_order.side_effect = APIError("fail")
        mock_http_client.cancel_order.side_effect = APIError("cancel fail")

        await strategy._evaluate_and_act(strategy.bid_order, Side.BUY)

        assert strategy._bid_in_flight is False


class TestShutdown:
    """shutdown のテスト."""

    @pytest.mark.asyncio
    async def test_shutdown_sets_event(self, config: Settings) -> None:
        """shutdown で _shutdown_event がセットされる."""
        strategy = MakerStrategy(config)

        await strategy.shutdown()

        assert strategy._shutdown_event.is_set()

    @pytest.mark.asyncio
    async def test_shutdown_idempotent(self, config: Settings) -> None:
        """shutdown は2回呼んでもエラーにならない."""
        strategy = MakerStrategy(config)

        await strategy.shutdown()
        await strategy.shutdown()

        assert strategy._shutdown_event.is_set()


class TestCleanup:
    """_cleanup のテスト."""

    @pytest.mark.asyncio
    async def test_cleanup_cancels_bid_order(
        self, config: Settings, mock_http_client: Mock
    ) -> None:
        """クリーンアップ時にbid注文がキャンセルされることを確認."""
        mock_http_client.cancel_order.return_value = {"code": 0}
        strategy = _make_strategy_with_mocks(config, mock_http_client)
        strategy.bid_order = _make_order(Side.BUY, 2498.0)
        strategy.ws_client = Mock()
        strategy.ws_client.disconnect = AsyncMock()

        await strategy._cleanup()

        mock_http_client.cancel_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_cancels_both_orders(
        self, config: Settings, mock_http_client: Mock
    ) -> None:
        """両サイドの注文がキャンセルされることを確認."""
        mock_http_client.cancel_order.return_value = {"code": 0}
        strategy = _make_strategy_with_mocks(config, mock_http_client)
        strategy.bid_order = _make_order(Side.BUY, 2498.0)
        strategy.ask_order = _make_order(Side.SELL, 2502.0)
        strategy.ask_order.id = "test_order_2"
        strategy.ws_client = Mock()
        strategy.ws_client.disconnect = AsyncMock()

        await strategy._cleanup()

        assert mock_http_client.cancel_order.call_count == 2

    @pytest.mark.asyncio
    async def test_cleanup_tolerates_cancel_failure(
        self, config: Settings, mock_http_client: Mock
    ) -> None:
        """キャンセル失敗時もクリーンアップが完了することを確認."""
        mock_http_client.cancel_order.side_effect = APIError("API error")
        strategy = _make_strategy_with_mocks(config, mock_http_client)
        strategy.bid_order = _make_order(Side.BUY, 2498.0)
        strategy.ws_client = Mock()
        strategy.ws_client.disconnect = AsyncMock()

        await strategy._cleanup()  # Should not raise

    @pytest.mark.asyncio
    async def test_cleanup_without_order_manager(self, config: Settings) -> None:
        """order_manager未初期化時もクリーンアップが完了することを確認."""
        strategy = MakerStrategy(config)
        strategy.ws_client = Mock()
        strategy.ws_client.disconnect = AsyncMock()

        await strategy._cleanup()  # Should not raise

    @pytest.mark.asyncio
    async def test_cleanup_disconnects_ws(self, config: Settings, mock_http_client: Mock) -> None:
        """WSクライアントが切断されることを確認."""
        mock_http_client.cancel_order.return_value = {"code": 0}
        strategy = _make_strategy_with_mocks(config, mock_http_client)
        strategy.ws_client = Mock()
        strategy.ws_client.disconnect = AsyncMock()

        await strategy._cleanup()

        strategy.ws_client.disconnect.assert_called_once()
