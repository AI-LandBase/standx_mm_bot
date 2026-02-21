"""厳格モード（リスク管理）モジュール."""

import asyncio
import logging
from typing import Any

from standx_mm_bot.client import StandXHTTPClient
from standx_mm_bot.config import Settings
from standx_mm_bot.models import Position, Side

logger = logging.getLogger(__name__)

# リトライ設定
MAX_RETRIES = 3
RETRY_INTERVAL_SEC = 0.5


class RiskManager:
    """
    厳格モード: 約定時の即座ポジションクローズ.

    約定 = 失敗。ポジションを持ってしまった場合、
    成行注文で即座にクローズし、建玉リスクをゼロに戻す。
    """

    def __init__(self, http_client: StandXHTTPClient, config: Settings):
        """
        RiskManagerを初期化.

        Args:
            http_client: StandX HTTPクライアント
            config: アプリケーション設定
        """
        self.client = http_client
        self.config = config
        self._lock = asyncio.Lock()

    async def close_position_immediately(self) -> bool:
        """
        成行でポジションを即座にクローズ（リトライ付き）.

        ポジションが存在する場合、反対サイドの成行注文で即座にクローズする。
        クローズ後にポジションゼロを確認し、残存していればリトライする。

        Returns:
            bool: ポジションがゼロになったら True、リトライ上限到達で False

        Raises:
            APIError: API呼び出しに失敗
        """
        async with self._lock:
            for attempt in range(1, MAX_RETRIES + 1):
                # ポジション取得
                response = await self.client.get_position(self.config.symbol)
                position = self._parse_position_response(response)

                if position is None:
                    logger.info("No position to close")
                    return True

                # 反対サイドで成行クローズ
                close_side = Side.SELL if position.side == Side.BUY else Side.BUY
                logger.error(
                    f"Closing position immediately (attempt {attempt}/{MAX_RETRIES}): "
                    f"side={position.side.value}, size={position.size}, "
                    f"entry_price={position.entry_price:.2f}, "
                    f"close_side={close_side.value}"
                )

                await self.client.new_order(
                    symbol=self.config.symbol,
                    side=close_side.value.lower(),
                    price=0,
                    size=position.size,
                    order_type="market",
                    time_in_force="ioc",
                    reduce_only=True,
                )

                # ポジションゼロ確認
                verify_response = await self.client.get_position(self.config.symbol)
                verify_position = self._parse_position_response(verify_response)

                if verify_position is None:
                    logger.info("Position closed successfully")
                    return True

                logger.warning(
                    f"Position still exists after close attempt {attempt}: "
                    f"size={verify_position.size}"
                )

                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_INTERVAL_SEC)

            logger.error(f"Failed to close position after {MAX_RETRIES} attempts")
            return False

    def _parse_position_response(self, response: dict[str, Any]) -> Position | None:
        """
        APIレスポンスをPositionにパース.

        Args:
            response: get_position() のAPIレスポンス

        Returns:
            Position: ポジション情報。ポジションなしの場合は None
        """
        # レスポンスがリスト形式の場合（空リスト = ポジションなし）
        if isinstance(response, list):
            if len(response) == 0:
                return None
            response = response[0]

        size = float(response.get("size", 0))
        if size == 0:
            return None

        side_str = response.get("side", "").upper()
        try:
            side = Side(side_str)
        except ValueError:
            logger.warning(f"Unknown position side: {side_str}")
            return None

        return Position(
            symbol=response.get("symbol", self.config.symbol),
            side=side,
            size=size,
            entry_price=float(response.get("entry_price", 0)),
            unrealized_pnl=float(response.get("unrealized_pnl", 0)),
        )
