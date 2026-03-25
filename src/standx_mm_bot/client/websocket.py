"""WebSocket クライアント."""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

from standx_mm_bot.client.exceptions import AuthenticationError
from standx_mm_bot.config import Settings

logger = logging.getLogger(__name__)


class StandXWebSocketClient:
    """StandX WebSocket クライアント."""

    def __init__(self, config: Settings, jwt_token: str | None = None):
        """
        WebSocketクライアントを初期化.

        Args:
            config: アプリケーション設定
            jwt_token: JWT認証トークン（order/tradeチャンネル購読に必要）
        """
        self.config = config
        self.jwt_token = jwt_token
        self.ws_url = "wss://perps.standx.com/ws-stream/v1"
        self.reconnect_interval = config.ws_reconnect_interval / 1000  # ms to seconds
        self.ws: ClientConnection | None = None
        self._running = False
        self._callbacks: dict[str, list[Callable[[dict[str, Any]], Awaitable[None]]]] = {
            "price": [],
            "order": [],
            "trade": [],
        }

    def on_price_update(self, callback: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        """
        価格更新コールバックを登録.

        Args:
            callback: 価格更新時に呼ばれる非同期関数
        """
        self._callbacks["price"].append(callback)

    def on_order_update(self, callback: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        """
        注文更新コールバックを登録.

        Args:
            callback: 注文更新時に呼ばれる非同期関数
        """
        self._callbacks["order"].append(callback)

    def on_trade(self, callback: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        """
        約定コールバックを登録.

        Args:
            callback: 約定時に呼ばれる非同期関数
        """
        self._callbacks["trade"].append(callback)

    async def _authenticate(self, ws: ClientConnection) -> None:
        """
        WebSocket接続を認証.

        jwt_tokenが未設定の場合はスキップする（後方互換性）。

        Args:
            ws: WebSocket接続

        Raises:
            AuthenticationError: 認証失敗時またはタイムアウト時
        """
        if self.jwt_token is None:
            return

        auth_message = {"auth": {"token": self.jwt_token}}
        await ws.send(json.dumps(auth_message))
        logger.info("Sent authentication message")

        try:
            raw_response = await asyncio.wait_for(ws.recv(), timeout=10.0)
            response = json.loads(raw_response)
            code = response.get("data", {}).get("code")
            if code != 200:
                msg = response.get("data", {}).get("msg", "unknown error")
                raise AuthenticationError(f"WebSocket authentication failed: {msg}")
            logger.info("WebSocket authentication successful")
        except TimeoutError:
            raise AuthenticationError("WebSocket authentication timed out") from None

    async def _subscribe_channels(self, ws: ClientConnection) -> None:
        """
        チャンネルを購読.

        Args:
            ws: WebSocket接続
        """
        # price チャンネル購読
        price_sub = {"subscribe": {"channel": "price", "symbol": self.config.symbol}}
        await ws.send(json.dumps(price_sub))
        logger.info(f"Subscribed to price channel: {self.config.symbol}")

        # order/trade チャンネルは認証済みの場合のみ購読
        if self.jwt_token is not None:
            # order チャンネル購読 (認証必要)
            order_sub = {"subscribe": {"channel": "order"}}
            await ws.send(json.dumps(order_sub))
            logger.info("Subscribed to order channel")

            # trade チャンネル購読 (認証必要)
            trade_sub = {"subscribe": {"channel": "trade"}}
            await ws.send(json.dumps(trade_sub))
            logger.info("Subscribed to trade channel")

    async def _dispatch_message(self, message: dict[str, Any]) -> None:
        """
        受信メッセージを適切なコールバックにディスパッチ.

        Args:
            message: 受信したメッセージ
        """
        channel = message.get("channel", "")

        # エラーメッセージをスキップ
        if "code" in message and message.get("code") != 200:
            logger.warning(f"WebSocket error message: {message}")
            return

        # price チャンネル
        if channel == "price":
            for callback in self._callbacks["price"]:
                try:
                    await callback(message.get("data", {}))
                except Exception as e:
                    logger.error(f"Error in price callback: {e}")

        # order チャンネル
        elif channel == "order":
            for callback in self._callbacks["order"]:
                try:
                    await callback(message.get("data", {}))
                except Exception as e:
                    logger.error(f"Error in order callback: {e}")

        # trade チャンネル
        elif channel == "trade":
            for callback in self._callbacks["trade"]:
                try:
                    await callback(message.get("data", {}))
                except Exception as e:
                    logger.error(f"Error in trade callback: {e}")

    async def _receive_messages(self, ws: ClientConnection) -> None:
        """
        メッセージを受信してディスパッチ.

        Args:
            ws: WebSocket接続
        """
        async for message in ws:
            if not self._running:
                break

            try:
                logger.debug(f"Received raw message: {message!r} (type: {type(message)})")
                # メッセージが既にstrの場合とbytesの場合を処理
                message_str = message.decode("utf-8") if isinstance(message, bytes) else message

                data = json.loads(message_str)
                logger.debug(f"Parsed message: {data}")
                await self._dispatch_message(data)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse WebSocket message: {e}, raw: {message!r}")
            except Exception as e:
                logger.error(f"Error processing WebSocket message: {e}")

    async def connect(self) -> None:
        """
        WebSocketに接続し、メッセージを受信.

        自動再接続機能付き。
        """
        self._running = True
        logger.info(f"Connecting to WebSocket: {self.ws_url}")

        while self._running:
            try:
                async with websockets.connect(self.ws_url) as ws:
                    self.ws = ws
                    logger.info("WebSocket connected")

                    await self._authenticate(ws)
                    await self._subscribe_channels(ws)
                    await self._receive_messages(ws)

            except websockets.ConnectionClosed:
                logger.warning("WebSocket disconnected, reconnecting...")
                await asyncio.sleep(self.reconnect_interval)

            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                await asyncio.sleep(self.reconnect_interval)

        logger.info("WebSocket client stopped")

    async def disconnect(self) -> None:
        """WebSocket接続を切断."""
        self._running = False
        if self.ws:
            await self.ws.close()
            logger.info("WebSocket disconnected")
