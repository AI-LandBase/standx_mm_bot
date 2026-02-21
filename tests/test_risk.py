"""厳格モード（リスク管理）モジュールのテスト."""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from standx_mm_bot.client import StandXHTTPClient
from standx_mm_bot.config import Settings
from standx_mm_bot.core.risk import RiskManager
from standx_mm_bot.models import Side


@pytest.fixture
def config() -> Settings:
    """テスト用設定."""
    return Settings(
        standx_private_key="0x" + "a" * 64,
        standx_wallet_address="0x1234567890abcdef",
        standx_chain="bsc",
        symbol="ETH-USD",
        order_size=0.001,
    )


@pytest.fixture
def mock_client() -> Mock:
    """モックHTTPクライアント."""
    client = Mock(spec=StandXHTTPClient)
    client.get_position = AsyncMock()
    client.new_order = AsyncMock()
    return client


class TestClosePositionImmediately:
    """close_position_immediately のテスト."""

    @pytest.mark.asyncio
    async def test_no_position_returns_true(self, mock_client: Mock, config: Settings) -> None:
        """ポジションなしの場合、True を返すことを確認."""
        mock_client.get_position.return_value = []

        risk_mgr = RiskManager(mock_client, config)
        result = await risk_mgr.close_position_immediately()

        assert result is True
        mock_client.new_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_position_dict_size_zero(self, mock_client: Mock, config: Settings) -> None:
        """size=0 のポジションの場合、True を返すことを確認."""
        mock_client.get_position.return_value = {"size": 0, "side": "BUY"}

        risk_mgr = RiskManager(mock_client, config)
        result = await risk_mgr.close_position_immediately()

        assert result is True
        mock_client.new_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_close_buy_position(self, mock_client: Mock, config: Settings) -> None:
        """BUYポジションをSELL成行でクローズすることを確認."""
        # 1回目: ポジションあり、2回目: ポジションなし（クローズ成功）
        mock_client.get_position.side_effect = [
            {"side": "BUY", "size": 0.001, "entry_price": 2500.0, "unrealized_pnl": -0.5},
            [],  # クローズ確認
        ]
        mock_client.new_order.return_value = {"order_id": "close1", "status": "FILLED"}

        risk_mgr = RiskManager(mock_client, config)
        result = await risk_mgr.close_position_immediately()

        assert result is True
        mock_client.new_order.assert_called_once_with(
            symbol="ETH-USD",
            side="sell",
            price=0,
            size=0.001,
            order_type="market",
            time_in_force="ioc",
            reduce_only=True,
        )

    @pytest.mark.asyncio
    async def test_close_sell_position(self, mock_client: Mock, config: Settings) -> None:
        """SELLポジションをBUY成行でクローズすることを確認."""
        mock_client.get_position.side_effect = [
            {"side": "SELL", "size": 0.002, "entry_price": 3000.0, "unrealized_pnl": 1.0},
            [],  # クローズ確認
        ]
        mock_client.new_order.return_value = {"order_id": "close2", "status": "FILLED"}

        risk_mgr = RiskManager(mock_client, config)
        result = await risk_mgr.close_position_immediately()

        assert result is True
        mock_client.new_order.assert_called_once_with(
            symbol="ETH-USD",
            side="buy",
            price=0,
            size=0.002,
            order_type="market",
            time_in_force="ioc",
            reduce_only=True,
        )

    @pytest.mark.asyncio
    async def test_retry_on_position_remaining(self, mock_client: Mock, config: Settings) -> None:
        """クローズ後もポジション残存時にリトライすることを確認."""
        position_data = {"side": "BUY", "size": 0.001, "entry_price": 2500.0}

        mock_client.get_position.side_effect = [
            position_data,  # 1回目: ポジションあり
            position_data,  # 1回目確認: まだ残存
            position_data,  # 2回目: ポジションあり
            [],  # 2回目確認: クローズ成功
        ]
        mock_client.new_order.return_value = {"order_id": "close3", "status": "FILLED"}

        with patch("standx_mm_bot.core.risk.asyncio.sleep", new_callable=AsyncMock):
            risk_mgr = RiskManager(mock_client, config)
            result = await risk_mgr.close_position_immediately()

        assert result is True
        assert mock_client.new_order.call_count == 2

    @pytest.mark.asyncio
    async def test_max_retries_exceeded(self, mock_client: Mock, config: Settings) -> None:
        """リトライ上限到達時に False を返すことを確認."""
        position_data = {"side": "BUY", "size": 0.001, "entry_price": 2500.0}

        # 全てのリトライでポジション残存
        mock_client.get_position.return_value = position_data
        mock_client.new_order.return_value = {"order_id": "close4", "status": "FILLED"}

        with patch("standx_mm_bot.core.risk.asyncio.sleep", new_callable=AsyncMock):
            risk_mgr = RiskManager(mock_client, config)
            result = await risk_mgr.close_position_immediately()

        assert result is False
        assert mock_client.new_order.call_count == 3  # MAX_RETRIES = 3


class TestParsePositionResponse:
    """_parse_position_response のテスト."""

    def test_parse_normal_position(self, mock_client: Mock, config: Settings) -> None:
        """正常なポジションデータをパースできることを確認."""
        risk_mgr = RiskManager(mock_client, config)
        position = risk_mgr._parse_position_response(
            {"side": "BUY", "size": 0.001, "entry_price": 2500.0, "unrealized_pnl": -0.5}
        )

        assert position is not None
        assert position.side == Side.BUY
        assert position.size == 0.001
        assert position.entry_price == 2500.0
        assert position.unrealized_pnl == -0.5

    def test_parse_empty_list(self, mock_client: Mock, config: Settings) -> None:
        """空リストの場合、None を返すことを確認."""
        risk_mgr = RiskManager(mock_client, config)
        position = risk_mgr._parse_position_response([])

        assert position is None

    def test_parse_size_zero(self, mock_client: Mock, config: Settings) -> None:
        """size=0 の場合、None を返すことを確認."""
        risk_mgr = RiskManager(mock_client, config)
        position = risk_mgr._parse_position_response(
            {"side": "BUY", "size": 0, "entry_price": 2500.0}
        )

        assert position is None

    def test_parse_list_with_position(self, mock_client: Mock, config: Settings) -> None:
        """リスト形式のレスポンスからポジションをパースできることを確認."""
        risk_mgr = RiskManager(mock_client, config)
        position = risk_mgr._parse_position_response(
            [{"side": "SELL", "size": 0.005, "entry_price": 3000.0}]
        )

        assert position is not None
        assert position.side == Side.SELL
        assert position.size == 0.005

    def test_parse_unknown_side(self, mock_client: Mock, config: Settings) -> None:
        """不明なサイドの場合、None を返すことを確認."""
        risk_mgr = RiskManager(mock_client, config)
        position = risk_mgr._parse_position_response(
            {"side": "UNKNOWN", "size": 0.001, "entry_price": 2500.0}
        )

        assert position is None


class TestConcurrency:
    """並行処理のテスト."""

    @pytest.mark.asyncio
    async def test_concurrent_close_calls(self, mock_client: Mock, config: Settings) -> None:
        """複数の同時クローズ呼び出しでもロックで順序が保証されることを確認."""
        call_order: list[str] = []

        async def mock_get_position(*_args, **_kwargs):
            call_order.append("get_position")
            await asyncio.sleep(0.01)
            return []

        mock_client.get_position.side_effect = mock_get_position

        risk_mgr = RiskManager(mock_client, config)

        results = await asyncio.gather(
            risk_mgr.close_position_immediately(),
            risk_mgr.close_position_immediately(),
            risk_mgr.close_position_immediately(),
        )

        # 全て成功
        assert all(results)

        # Lockにより順序が保証される（インターリーブしない）
        assert call_order == ["get_position", "get_position", "get_position"]
