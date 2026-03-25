"""Maker戦略モジュール."""

import asyncio
import logging

from standx_mm_bot.config import Settings
from standx_mm_bot.core.distance import (
    calculate_distance_bps,
    calculate_target_price,
    is_approaching,
)
from standx_mm_bot.models import Action, Order, Side

logger = logging.getLogger(__name__)


class MakerStrategy:
    """Maker戦略: 全コンポーネントを統合し、Bot を駆動する."""

    def __init__(self, config: Settings):
        self.config = config
        self.mark_price: float = 0.0
        self.bid_order: Order | None = None
        self.ask_order: Order | None = None
        self._shutdown_event = asyncio.Event()
        self._bid_in_flight: bool = False
        self._ask_in_flight: bool = False
        self._initial_orders_placed: bool = False
        self._exit_code: int = 0

        # run() 内で初期化されるコンポーネント
        self.http_client = None
        self.ws_client = None
        self.order_manager = None
        self.risk_manager = None

    def evaluate_order(self, order: Order, mark_price: float, side: Side) -> Action:
        """注文の状態を評価し、実行すべきアクションを決定."""
        distance = calculate_distance_bps(order.price, mark_price)

        # 優先順位1: 約定回避 (ESCAPE)
        if is_approaching(mark_price, order.price, side):
            if distance < self.config.escape_threshold_bps:
                return Action.ESCAPE

        # 優先順位2: 10bps 境界への接近 (REPOSITION)
        if distance > (10 - self.config.reposition_threshold_bps):
            return Action.REPOSITION

        # 優先順位3: 目標価格からの乖離 (REPOSITION)
        target_price = calculate_target_price(mark_price, side, self.config.target_distance_bps)
        price_diff_bps = abs(order.price - target_price) / mark_price * 10000
        if price_diff_bps > self.config.price_move_threshold_bps:
            return Action.REPOSITION

        return Action.HOLD
