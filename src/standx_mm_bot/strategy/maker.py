"""Maker戦略モジュール."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from standx_mm_bot.client import StandXHTTPClient, StandXWebSocketClient
from standx_mm_bot.client.exceptions import APIError
from standx_mm_bot.config import Settings
from standx_mm_bot.core.distance import (
    calculate_distance_bps,
    calculate_target_price,
    is_approaching,
)
from standx_mm_bot.core.escape import calculate_escape_price
from standx_mm_bot.core.order import OrderManager
from standx_mm_bot.core.risk import RiskManager
from standx_mm_bot.models import Action, Order, OrderStatus, Side

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
        self.http_client: StandXHTTPClient | None = None
        self.ws_client: StandXWebSocketClient | None = None
        self.order_manager: OrderManager | None = None
        self.risk_manager: RiskManager | None = None

    def evaluate_order(self, order: Order, mark_price: float, side: Side) -> Action:
        """注文の状態を評価し、実行すべきアクションを決定."""
        distance = calculate_distance_bps(order.price, mark_price)

        # 優先順位1: 約定回避 (ESCAPE)
        if (
            is_approaching(mark_price, order.price, side)
            and distance < self.config.escape_threshold_bps
        ):
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

    # ------------------------------------------------------------------
    # コールバック: 価格更新
    # ------------------------------------------------------------------

    async def _on_price_update(self, data: dict[str, Any]) -> None:
        """価格更新コールバック.

        Args:
            data: WebSocket から受信した価格データ
        """
        raw = data.get("mark_price")
        if raw is None:
            return
        self.mark_price = float(raw)

        # 初回: 初期注文を発注
        if not self._initial_orders_placed:
            await self._place_initial_orders()
            self._initial_orders_placed = True
            return

        # 片側欠落時: 再発注
        if self.bid_order is None:
            await self._replace_order(Side.BUY)
        if self.ask_order is None:
            await self._replace_order(Side.SELL)

        # 既存注文の評価
        if self.bid_order is not None:
            await self._evaluate_and_act(self.bid_order, Side.BUY)
        if self.ask_order is not None:
            await self._evaluate_and_act(self.ask_order, Side.SELL)

    # ------------------------------------------------------------------
    # コールバック: 注文更新
    # ------------------------------------------------------------------

    async def _on_order_update(self, data: dict[str, Any]) -> None:
        """注文更新コールバック.

        Args:
            data: WebSocket から受信した注文データ
        """
        order_id = data.get("order_id")
        status_str = data.get("status", "")

        # bid / ask のどちらに該当するか判定
        target_order, side = self._match_order(order_id)
        if target_order is None:
            return

        if status_str == "CANCELED":
            if side == Side.BUY:
                self.bid_order = None
            else:
                self.ask_order = None
            logger.info(f"Order canceled via WS: order_id={order_id}")

        elif status_str == "FILLED":
            target_order.status = OrderStatus.FILLED
            logger.warning(f"Order filled via WS: order_id={order_id}")

        elif status_str == "PARTIALLY_FILLED":
            target_order.status = OrderStatus.PARTIALLY_FILLED
            filled = data.get("filled_size")
            if filled is not None:
                target_order.filled_size = float(filled)
            logger.warning(
                f"Order partially filled via WS: order_id={order_id}, "
                f"filled_size={target_order.filled_size}"
            )

    # ------------------------------------------------------------------
    # コールバック: 約定検知 (厳格モード)
    # ------------------------------------------------------------------

    async def _on_trade(self, data: dict[str, Any]) -> None:
        """約定検知コールバック.

        約定 = 失敗。即座にポジションをクローズし、Bot を停止する。

        Args:
            data: WebSocket から受信した約定データ
        """
        order_id = data.get("order_id")
        target_order, _side = self._match_order(order_id)
        if target_order is None:
            return

        logger.error(
            f"TRADE DETECTED: order_id={order_id} — 約定は失敗。即座にクローズし Bot を停止します。"
        )

        if self.risk_manager is not None:
            await self.risk_manager.close_position_immediately()

        self._exit_code = 1
        await self.shutdown()

    # ------------------------------------------------------------------
    # 評価 → アクション実行
    # ------------------------------------------------------------------

    async def _evaluate_and_act(self, order: Order, side: Side) -> None:
        """注文を評価し、必要なアクションを実行.

        Args:
            order: 評価対象の注文
            side: 注文サイド
        """
        # in_flight チェック
        if side == Side.BUY and self._bid_in_flight:
            return
        if side == Side.SELL and self._ask_in_flight:
            return

        # in_flight フラグを立てる
        if side == Side.BUY:
            self._bid_in_flight = True
        else:
            self._ask_in_flight = True

        try:
            action = self.evaluate_order(order, self.mark_price, side)
            if action == Action.HOLD:
                return

            logger.info(
                f"Action required: side={side.value}, action={action.value}, order_id={order.id}"
            )
            new_order = await self._execute_action(action, order, side)

            # 注文更新
            if side == Side.BUY:
                self.bid_order = new_order if new_order is not None else self.bid_order
            else:
                self.ask_order = new_order if new_order is not None else self.ask_order
        finally:
            if side == Side.BUY:
                self._bid_in_flight = False
            else:
                self._ask_in_flight = False

    async def _execute_action(self, action: Action, order: Order, side: Side) -> Order | None:
        """アクションを実行.

        Args:
            action: 実行するアクション
            order: 対象注文
            side: 注文サイド

        Returns:
            新規注文。変更なしの場合は None。
        """
        if action == Action.HOLD:
            return None

        if self.order_manager is None:
            logger.error("OrderManager is not initialized")
            return None

        if action == Action.ESCAPE:
            return await self._execute_escape(order, side)

        if action == Action.REPOSITION:
            return await self._execute_reposition(order, side)

        return None

    async def _execute_escape(self, order: Order, side: Side) -> Order | None:
        """ESCAPE アクションを実行.

        Args:
            order: 対象注文
            side: 注文サイド

        Returns:
            新規注文、またはキャンセルフォールバック時は None
        """
        assert self.order_manager is not None
        escape_price = calculate_escape_price(
            self.mark_price, side, self.config.outer_escape_distance_bps
        )
        try:
            new_order = await self.order_manager.reposition_order(
                old_order_id=order.id,
                new_price=escape_price,
                side=side,
                size=self.config.order_size,
                strategy="place_first",
            )
            logger.info(f"Escaped: order_id={new_order.id}, price={escape_price:.2f}")
            return new_order
        except APIError:
            logger.warning(
                f"Escape reposition failed for order_id={order.id}, falling back to cancel"
            )
            try:
                await self.order_manager.cancel_order(order.id)
            except APIError:
                logger.error(f"Escape cancel fallback also failed: order_id={order.id}")
            return None

    async def _execute_reposition(self, order: Order, side: Side) -> Order | None:
        """REPOSITION アクションを実行.

        Args:
            order: 対象注文
            side: 注文サイド

        Returns:
            新規注文、または失敗時 None
        """
        assert self.order_manager is not None
        target_price = calculate_target_price(
            self.mark_price, side, self.config.target_distance_bps
        )
        try:
            new_order = await self.order_manager.reposition_order(
                old_order_id=order.id,
                new_price=target_price,
                side=side,
                size=self.config.order_size,
                strategy="place_first",
            )
            logger.info(f"Repositioned: order_id={new_order.id}, price={target_price:.2f}")
            return new_order
        except APIError:
            logger.error(f"Reposition failed for order_id={order.id}, side={side.value}")
            return None

    # ------------------------------------------------------------------
    # 初期注文 / 補充
    # ------------------------------------------------------------------

    async def _place_initial_orders(self) -> None:
        """初期注文を BUY / SELL 両サイド発注."""
        assert self.order_manager is not None

        # BUY
        try:
            bid_price = calculate_target_price(
                self.mark_price, Side.BUY, self.config.target_distance_bps
            )
            self.bid_order = await self.order_manager.place_order(
                side=Side.BUY,
                price=bid_price,
                size=self.config.order_size,
            )
            logger.info(f"Initial BUY order placed: price={bid_price:.2f}")
        except APIError:
            logger.error("Failed to place initial BUY order")

        # SELL
        try:
            ask_price = calculate_target_price(
                self.mark_price, Side.SELL, self.config.target_distance_bps
            )
            self.ask_order = await self.order_manager.place_order(
                side=Side.SELL,
                price=ask_price,
                size=self.config.order_size,
            )
            logger.info(f"Initial SELL order placed: price={ask_price:.2f}")
        except APIError:
            logger.error("Failed to place initial SELL order")

    async def _replace_order(self, side: Side) -> None:
        """欠落している片側の注文を再発注.

        Args:
            side: 発注するサイド
        """
        if self.order_manager is None:
            return
        try:
            target_price = calculate_target_price(
                self.mark_price, side, self.config.target_distance_bps
            )
            order = await self.order_manager.place_order(
                side=side,
                price=target_price,
                size=self.config.order_size,
            )
            if side == Side.BUY:
                self.bid_order = order
            else:
                self.ask_order = order
            logger.info(f"Replaced {side.value} order: price={target_price:.2f}")
        except APIError:
            logger.error(f"Failed to replace {side.value} order")

    # ------------------------------------------------------------------
    # ヘルパー
    # ------------------------------------------------------------------

    def _match_order(self, order_id: str | None) -> tuple[Order | None, Side | None]:
        """order_id から bid/ask のどちらかを特定.

        Args:
            order_id: 検索する注文ID

        Returns:
            (Order, Side) のタプル。見つからない場合は (None, None)。
        """
        if order_id is not None:
            if self.bid_order is not None and self.bid_order.id == order_id:
                return self.bid_order, Side.BUY
            if self.ask_order is not None and self.ask_order.id == order_id:
                return self.ask_order, Side.SELL
        return None, None

    # ------------------------------------------------------------------
    # ライフサイクル
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """メインループ."""
        async with StandXHTTPClient(self.config) as http_client:
            self.http_client = http_client
            self.order_manager = OrderManager(http_client, self.config)
            self.risk_manager = RiskManager(http_client, self.config)

            ws_client = StandXWebSocketClient(self.config, jwt_token=http_client.jwt_token)
            self.ws_client = ws_client

            ws_client.on_price_update(self._on_price_update)
            ws_client.on_order_update(self._on_order_update)
            ws_client.on_trade(self._on_trade)

            logger.info(f"MakerStrategy started: symbol={self.config.symbol}")

            ws_task = asyncio.create_task(ws_client.connect())
            await self._shutdown_event.wait()
            await self._cleanup()
            ws_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ws_task

            logger.info("MakerStrategy stopped")

    async def shutdown(self) -> None:
        """グレースフルシャットダウン."""
        if self._shutdown_event.is_set():
            return
        logger.info("Shutting down MakerStrategy...")
        self._shutdown_event.set()

    async def _cleanup(self) -> None:
        """残注文キャンセル・WS切断."""
        if self.order_manager is not None:
            if self.bid_order is not None:
                try:
                    await self.order_manager.cancel_order(self.bid_order.id)
                    logger.info(f"Cancelled BUY order: {self.bid_order.id}")
                except Exception as e:
                    logger.error(f"Failed to cancel BUY order: {e}")
            if self.ask_order is not None:
                try:
                    await self.order_manager.cancel_order(self.ask_order.id)
                    logger.info(f"Cancelled SELL order: {self.ask_order.id}")
                except Exception as e:
                    logger.error(f"Failed to cancel SELL order: {e}")

        if self.ws_client is not None:
            await self.ws_client.disconnect()
