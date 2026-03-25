"""MakerStrategy のテスト."""

import pytest

from standx_mm_bot.config import Settings
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


def _make_order(side: Side, price: float) -> Order:
    """テスト用注文を作成."""
    return Order(
        id="test_order_1",
        symbol="ETH-USD",
        side=side,
        price=price,
        size=0.001,
        order_type=OrderType.LIMIT,
        status=OrderStatus.OPEN,
    )


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
