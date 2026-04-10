"""WebSocketクライアントのテスト."""

import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest

from standx_mm_bot.client import StandXWebSocketClient
from standx_mm_bot.config import Settings


class FakeWS:
    """_receive_messages テスト用の偽WebSocket."""

    def __init__(self, messages: list[Any]) -> None:
        self._messages = messages

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[Any]:
        for m in self._messages:
            yield m


@pytest.fixture
def config() -> Settings:
    """テスト用の設定を作成."""
    return Settings(
        standx_private_key="0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
        standx_wallet_address="test_wallet_address",
        symbol="ETH-USD",
        ws_reconnect_interval=1000,  # 1秒
    )


@pytest.mark.asyncio
async def test_websocket_initialization(config: Settings) -> None:
    """WebSocketクライアントが正しく初期化されることを確認."""
    client = StandXWebSocketClient(config)

    assert client.config == config
    assert client.ws_url == "wss://perps.standx.com/ws-stream/v1"
    assert client.reconnect_interval == 1.0  # 1000ms -> 1.0s
    assert client.ws is None
    assert client._running is False


@pytest.mark.asyncio
async def test_callback_registration(config: Settings) -> None:
    """コールバックが正しく登録されることを確認."""
    client = StandXWebSocketClient(config)

    price_callback_called = False
    order_callback_called = False
    trade_callback_called = False

    async def price_callback(_data: dict) -> None:
        nonlocal price_callback_called
        price_callback_called = True

    async def order_callback(_data: dict) -> None:
        nonlocal order_callback_called
        order_callback_called = True

    async def trade_callback(_data: dict) -> None:
        nonlocal trade_callback_called
        trade_callback_called = True

    client.on_price_update(price_callback)
    client.on_order_update(order_callback)
    client.on_trade(trade_callback)

    assert len(client._callbacks["price"]) == 1
    assert len(client._callbacks["order"]) == 1
    assert len(client._callbacks["trade"]) == 1

    # コールバックが呼ばれることを確認
    await client._dispatch_message({"channel": "price", "data": {}})
    assert price_callback_called

    await client._dispatch_message({"channel": "order", "data": {}})
    assert order_callback_called

    await client._dispatch_message({"channel": "trade", "data": {}})
    assert trade_callback_called


@pytest.mark.asyncio
async def test_dispatch_message_price(config: Settings) -> None:
    """priceチャンネルのメッセージが正しくディスパッチされることを確認."""
    client = StandXWebSocketClient(config)

    received_data = None

    async def price_callback(data: dict) -> None:
        nonlocal received_data
        received_data = data

    client.on_price_update(price_callback)

    test_data = {"mark_price": "3500.0", "symbol": "ETH-USD"}
    await client._dispatch_message({"channel": "price", "data": test_data})

    assert received_data == test_data


@pytest.mark.asyncio
async def test_dispatch_message_order(config: Settings) -> None:
    """orderチャンネルのメッセージが正しくディスパッチされることを確認."""
    client = StandXWebSocketClient(config)

    received_data = None

    async def order_callback(data: dict) -> None:
        nonlocal received_data
        received_data = data

    client.on_order_update(order_callback)

    test_data = {"order_id": "123", "status": "FILLED"}
    await client._dispatch_message({"channel": "order", "data": test_data})

    assert received_data == test_data


@pytest.mark.asyncio
async def test_dispatch_message_trade(config: Settings) -> None:
    """tradeチャンネルのメッセージが正しくディスパッチされることを確認."""
    client = StandXWebSocketClient(config)

    received_data = None

    async def trade_callback(data: dict) -> None:
        nonlocal received_data
        received_data = data

    client.on_trade(trade_callback)

    test_data = {"trade_id": "456", "price": "3500.0"}
    await client._dispatch_message({"channel": "trade", "data": test_data})

    assert received_data == test_data


@pytest.mark.asyncio
async def test_subscribe_channels(config: Settings) -> None:
    """チャンネル購読が正しく送信されることを確認."""
    client = StandXWebSocketClient(config, jwt_token="test_token")

    # WebSocketのモックを作成
    ws_mock = AsyncMock()
    sent_messages = []

    async def mock_send(message: str) -> None:
        sent_messages.append(json.loads(message))

    ws_mock.send = mock_send

    await client._subscribe_channels(ws_mock)

    assert len(sent_messages) == 3

    # price チャンネル購読
    assert sent_messages[0] == {"subscribe": {"channel": "price", "symbol": "ETH-USD"}}

    # order チャンネル購読
    assert sent_messages[1] == {"subscribe": {"channel": "order"}}

    # trade チャンネル購読
    assert sent_messages[2] == {"subscribe": {"channel": "trade"}}


@pytest.mark.asyncio
async def test_websocket_initialization_with_jwt(config: Settings) -> None:
    """JWT付きでWebSocketクライアントが初期化されることを確認."""
    client = StandXWebSocketClient(config, jwt_token="test_jwt_token")
    assert client.jwt_token == "test_jwt_token"


@pytest.mark.asyncio
async def test_websocket_initialization_without_jwt(config: Settings) -> None:
    """JWT無しでWebSocketクライアントが初期化されることを確認（後方互換性）."""
    client = StandXWebSocketClient(config)
    assert client.jwt_token is None


@pytest.mark.asyncio
async def test_authenticate_sends_auth_message(config: Settings) -> None:
    """認証メッセージが正しく送信されることを確認."""
    client = StandXWebSocketClient(config, jwt_token="test_jwt_token")
    ws_mock = AsyncMock()
    sent_messages = []

    async def mock_send(message: str) -> None:
        sent_messages.append(json.loads(message))

    ws_mock.send = mock_send
    auth_response = json.dumps(
        {"seq": 1, "channel": "auth", "data": {"code": 200, "msg": "success"}}
    )
    ws_mock.recv = AsyncMock(return_value=auth_response)

    await client._authenticate(ws_mock)

    assert len(sent_messages) == 1
    assert sent_messages[0] == {"auth": {"token": "test_jwt_token"}}


@pytest.mark.asyncio
async def test_authenticate_skipped_without_jwt(config: Settings) -> None:
    """JWT未設定時は認証をスキップすることを確認."""
    client = StandXWebSocketClient(config)
    ws_mock = AsyncMock()

    await client._authenticate(ws_mock)

    ws_mock.send.assert_not_called()


@pytest.mark.asyncio
async def test_authenticate_failure_raises(config: Settings) -> None:
    """認証失敗時にAuthenticationErrorが発生することを確認."""
    from standx_mm_bot.client.exceptions import AuthenticationError

    client = StandXWebSocketClient(config, jwt_token="bad_token")
    ws_mock = AsyncMock()
    ws_mock.send = AsyncMock()
    auth_response = json.dumps(
        {"seq": 1, "channel": "auth", "data": {"code": 401, "msg": "unauthorized"}}
    )
    ws_mock.recv = AsyncMock(return_value=auth_response)

    with pytest.raises(AuthenticationError):
        await client._authenticate(ws_mock)


@pytest.mark.asyncio
async def test_subscribe_channels_without_jwt(config: Settings) -> None:
    """JWT未設定時はpriceチャンネルのみ購読されることを確認."""
    client = StandXWebSocketClient(config)  # jwt_token=None
    ws_mock = AsyncMock()
    sent_messages = []

    async def mock_send(message: str) -> None:
        sent_messages.append(json.loads(message))

    ws_mock.send = mock_send

    await client._subscribe_channels(ws_mock)

    assert len(sent_messages) == 1
    assert sent_messages[0] == {"subscribe": {"channel": "price", "symbol": "ETH-USD"}}


@pytest.mark.asyncio
async def test_disconnect(config: Settings) -> None:
    """切断が正しく動作することを確認."""
    client = StandXWebSocketClient(config)
    client._running = True
    client.ws = AsyncMock()

    await client.disconnect()

    assert client._running is False
    client.ws.close.assert_called_once()


@pytest.mark.asyncio
async def test_callback_error_handling(config: Settings) -> None:
    """コールバックでエラーが発生しても他のコールバックが実行されることを確認."""
    client = StandXWebSocketClient(config)

    callback2_called = False

    async def failing_callback(_data: dict) -> None:
        raise ValueError("Test error")

    async def successful_callback(_data: dict) -> None:
        nonlocal callback2_called
        callback2_called = True

    client.on_price_update(failing_callback)
    client.on_price_update(successful_callback)

    # エラーが発生しても2番目のコールバックは実行される
    await client._dispatch_message({"channel": "price", "data": {}})

    assert callback2_called


@pytest.mark.asyncio
async def test_dispatch_error_message_skipped(config: Settings) -> None:
    """エラーメッセージがスキップされることを確認."""
    client = StandXWebSocketClient(config)

    received = False

    async def callback(_data: dict) -> None:
        nonlocal received
        received = True

    client.on_price_update(callback)

    # code != 200 のエラーメッセージ
    await client._dispatch_message({"code": 401, "msg": "unauthorized"})

    assert not received


@pytest.mark.asyncio
async def test_receive_messages(config: Settings) -> None:
    """_receive_messagesがメッセージを正しくディスパッチすることを確認."""
    client = StandXWebSocketClient(config)
    client._running = True

    received_data: list[dict] = []

    async def price_callback(data: dict) -> None:
        received_data.append(data)
        # 1件受信後に停止
        client._running = False

    client.on_price_update(price_callback)

    # AsyncIterator を返すモック
    messages = [json.dumps({"channel": "price", "data": {"mark_price": "3500.0"}})]

    ws_mock = FakeWS(messages)

    await client._receive_messages(ws_mock)

    assert len(received_data) == 1
    assert received_data[0]["mark_price"] == "3500.0"


@pytest.mark.asyncio
async def test_receive_messages_bytes(config: Settings) -> None:
    """バイト列メッセージも正しく処理されることを確認."""
    client = StandXWebSocketClient(config)
    client._running = True

    received_data: list[dict] = []

    async def price_callback(data: dict) -> None:
        received_data.append(data)
        client._running = False

    client.on_price_update(price_callback)

    messages = [json.dumps({"channel": "price", "data": {"mark_price": "4000.0"}}).encode()]

    ws_mock = FakeWS(messages)

    await client._receive_messages(ws_mock)

    assert len(received_data) == 1
    assert received_data[0]["mark_price"] == "4000.0"


@pytest.mark.asyncio
async def test_receive_messages_invalid_json(config: Settings) -> None:
    """不正なJSONメッセージがエラーにならないことを確認."""
    client = StandXWebSocketClient(config)
    client._running = True

    messages = ["not valid json"]

    ws_mock = FakeWS(messages)

    # エラーが発生せず正常終了
    await client._receive_messages(ws_mock)


@pytest.mark.asyncio
async def test_authenticate_timeout(config: Settings) -> None:
    """認証タイムアウト時にAuthenticationErrorが発生することを確認."""
    import asyncio

    from standx_mm_bot.client.exceptions import AuthenticationError

    client = StandXWebSocketClient(config, jwt_token="test_token")
    ws_mock = AsyncMock()
    ws_mock.send = AsyncMock()

    async def slow_recv() -> str:
        await asyncio.sleep(30)
        return ""

    ws_mock.recv = slow_recv

    with pytest.raises(AuthenticationError, match="timed out"):
        await client._authenticate(ws_mock)
