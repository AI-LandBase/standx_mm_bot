"""REST APIクライアントのテスト."""

from unittest.mock import patch

import aiohttp
import pytest
from aioresponses import aioresponses

from standx_mm_bot.client import (
    APIError,
    AuthenticationError,
    StandXHTTPClient,
)
from standx_mm_bot.config import Settings


@pytest.fixture
def config() -> Settings:
    """テスト用設定を生成."""
    return Settings(
        standx_private_key="0x" + "a" * 64,
        standx_wallet_address="0x1234567890abcdef",
        standx_chain="bsc",
        symbol="ETH_USDC",
        order_size=0.1,
    )


@pytest.mark.asyncio
async def test_context_manager(config: Settings) -> None:
    """コンテキストマネージャーが正しく動作することを確認."""
    # テスト用のJWTトークンを指定
    async with StandXHTTPClient(config, jwt_token="test_jwt_token") as client:
        assert client.session is not None

    # コンテキスト終了後はセッションがクローズされる
    assert client.session.closed


@pytest.mark.asyncio
async def test_get_symbol_price(config: Settings) -> None:
    """シンボル価格取得が正しく動作することを確認."""
    with aioresponses() as mocked:
        # モックレスポンスを設定
        mocked.get(
            "https://perps.standx.com/api/query_symbol_price?symbol=ETH_USDC",
            payload={
                "symbol": "ETH_USDC",
                "mark_price": 3500.0,
                "index_price": 3498.5,
            },
        )

        async with StandXHTTPClient(config, jwt_token="test_jwt_token") as client:
            response = await client.get_symbol_price("ETH_USDC")

        assert response["symbol"] == "ETH_USDC"
        assert response["mark_price"] == 3500.0


@pytest.mark.asyncio
async def test_new_order(config: Settings) -> None:
    """注文発注が正しく動作することを確認."""
    with aioresponses() as mocked:
        # モックレスポンスを設定
        mocked.post(
            "https://perps.standx.com/api/new_order",
            payload={
                "order_id": "order_123",
                "symbol": "ETH_USDC",
                "side": "BUY",
                "price": 3500.0,
                "size": 0.1,
                "order_type": "LIMIT",
                "status": "OPEN",
            },
        )

        async with StandXHTTPClient(config, jwt_token="test_jwt_token") as client:
            response = await client.new_order(
                symbol="ETH_USDC",
                side="BUY",
                price=3500.0,
                size=0.1,
            )

        assert response["order_id"] == "order_123"
        assert response["status"] == "OPEN"


@pytest.mark.asyncio
async def test_cancel_order(config: Settings) -> None:
    """注文キャンセルが正しく動作することを確認."""
    with aioresponses() as mocked:
        # モックレスポンスを設定
        mocked.post(
            "https://perps.standx.com/api/cancel_order",
            payload={
                "order_id": "order_123",
                "symbol": "ETH_USDC",
                "status": "CANCELED",
            },
        )

        async with StandXHTTPClient(config, jwt_token="test_jwt_token") as client:
            response = await client.cancel_order(
                order_id="order_123",
                symbol="ETH_USDC",
            )

        assert response["order_id"] == "order_123"
        assert response["status"] == "CANCELED"


@pytest.mark.asyncio
async def test_get_open_orders(config: Settings) -> None:
    """未決注文一覧取得が正しく動作することを確認."""
    with aioresponses() as mocked:
        # モックレスポンスを設定
        mocked.get(
            "https://perps.standx.com/api/query_open_orders?symbol=ETH_USDC",
            payload={
                "orders": [
                    {"order_id": "order_1", "side": "BUY", "price": 3500.0},
                    {"order_id": "order_2", "side": "SELL", "price": 3510.0},
                ],
            },
        )

        async with StandXHTTPClient(config, jwt_token="test_jwt_token") as client:
            response = await client.get_open_orders("ETH_USDC")

        assert len(response["orders"]) == 2
        assert response["orders"][0]["order_id"] == "order_1"


@pytest.mark.asyncio
async def test_get_position(config: Settings) -> None:
    """ポジション情報取得が正しく動作することを確認."""
    with aioresponses() as mocked:
        # モックレスポンスを設定
        mocked.get(
            "https://perps.standx.com/api/query_positions?symbol=ETH_USDC",
            payload={
                "symbol": "ETH_USDC",
                "side": "LONG",
                "size": 1.0,
                "entry_price": 3500.0,
            },
        )

        async with StandXHTTPClient(config, jwt_token="test_jwt_token") as client:
            response = await client.get_position("ETH_USDC")

        assert response["symbol"] == "ETH_USDC"
        assert response["size"] == 1.0


@pytest.mark.asyncio
async def test_get_balance(config: Settings) -> None:
    """残高情報取得が正しく動作することを確認."""
    with aioresponses() as mocked:
        # モックレスポンスを設定
        mocked.get(
            "https://perps.standx.com/api/query_balance",
            payload={
                "equity": 10000.0,
                "cross_available": 8000.0,
                "upnl": 500.0,
                "locked": 2000.0,
                "balance": 9500.0,
            },
        )

        async with StandXHTTPClient(config, jwt_token="test_jwt_token") as client:
            response = await client.get_balance()

        assert response["equity"] == 10000.0
        assert response["cross_available"] == 8000.0
        assert response["upnl"] == 500.0
        assert response["locked"] == 2000.0


@pytest.mark.asyncio
async def test_authentication_error(config: Settings) -> None:
    """認証エラー (401) が正しく処理されることを確認."""
    with aioresponses() as mocked:
        # 401エラーを返すモックレスポンス
        mocked.get(
            "https://perps.standx.com/api/query_symbol_price?symbol=ETH_USDC",
            status=401,
        )

        async with StandXHTTPClient(config, jwt_token="test_jwt_token") as client:
            with pytest.raises(AuthenticationError, match="JWT expired or invalid"):
                await client.get_symbol_price("ETH_USDC")


@pytest.mark.asyncio
async def test_rate_limit_retry(config: Settings) -> None:
    """レート制限 (429) 時にリトライされることを確認."""
    with aioresponses() as mocked:
        # 1回目: 429エラー
        mocked.get(
            "https://perps.standx.com/api/query_symbol_price?symbol=ETH_USDC",
            status=429,
        )
        # 2回目: 成功
        mocked.get(
            "https://perps.standx.com/api/query_symbol_price?symbol=ETH_USDC",
            payload={"symbol": "ETH_USDC", "mark_price": 3500.0},
        )

        # asyncio.sleepをモック
        with patch("asyncio.sleep", return_value=None):
            async with StandXHTTPClient(config, jwt_token="test_jwt_token") as client:
                response = await client.get_symbol_price("ETH_USDC")

        # リトライ後に成功
        assert response["mark_price"] == 3500.0


@pytest.mark.asyncio
async def test_api_error(config: Settings) -> None:
    """APIエラー (その他のステータスコード) が正しく処理されることを確認."""
    with aioresponses() as mocked:
        # 400エラーを返すモックレスポンス
        mocked.get(
            "https://perps.standx.com/api/query_symbol_price?symbol=ETH_USDC",
            status=400,
            body="Bad Request",
        )

        async with StandXHTTPClient(config, jwt_token="test_jwt_token") as client:
            with pytest.raises(APIError, match="HTTP 400"):
                await client.get_symbol_price("ETH_USDC")


@pytest.mark.asyncio
async def test_session_not_initialized(config: Settings) -> None:
    """セッション未初期化時にエラーが発生することを確認."""
    client = StandXHTTPClient(config, jwt_token="test_jwt_token")

    with pytest.raises(RuntimeError, match="Session not initialized"):
        await client.get_symbol_price("ETH_USDC")


@pytest.mark.asyncio
async def test_jwt_token_not_initialized(config: Settings) -> None:
    """JWTトークン未設定時にエラーが発生することを確認."""
    async with StandXHTTPClient(config, jwt_token="test_jwt_token") as client:
        client.jwt_token = None
        with pytest.raises(RuntimeError, match="JWT token not initialized"):
            await client.get_symbol_price("ETH_USDC")


@pytest.mark.asyncio
async def test_bsc_missing_signing_key() -> None:
    """BSCチェーンでリクエスト署名鍵が未設定時にエラーが発生することを確認."""
    config_no_key = Settings(
        standx_private_key="0x" + "a" * 64,
        standx_wallet_address="0x1234567890abcdef",
        standx_chain="bsc",
        symbol="ETH_USDC",
        order_size=0.1,
        standx_request_signing_key="",
    )
    async with StandXHTTPClient(config_no_key, jwt_token="test_jwt_token") as client:
        with pytest.raises(RuntimeError, match="STANDX_REQUEST_SIGNING_KEY"):
            await client.get_symbol_price("ETH_USDC")


@pytest.mark.asyncio
async def test_network_error(config: Settings) -> None:
    """ネットワークエラーが正しくNetworkErrorに変換されることを確認."""
    from standx_mm_bot.client.exceptions import NetworkError

    with aioresponses() as mocked:
        mocked.get(
            "https://perps.standx.com/api/query_symbol_price?symbol=ETH_USDC",
            exception=aiohttp.ClientError("Connection refused"),
        )

        async with StandXHTTPClient(config, jwt_token="test_jwt_token") as client:
            with pytest.raises(NetworkError, match="Network error"):
                await client.get_symbol_price("ETH_USDC")


@pytest.mark.asyncio
async def test_obtain_jwt_prepare_signin_failure(config: Settings) -> None:
    """JWT取得時にprepare-signinが失敗した場合のエラーを確認."""
    with aioresponses() as mocked:
        mocked.post(
            "https://api.standx.com/v1/offchain/prepare-signin?chain=bsc",
            status=500,
            body="Internal Server Error",
        )

        with pytest.raises(AuthenticationError, match="Failed to prepare signin"):
            async with StandXHTTPClient(config) as _client:
                pass


@pytest.mark.asyncio
async def test_obtain_jwt_no_signed_data(config: Settings) -> None:
    """JWT取得時にsignedDataが返されない場合のエラーを確認."""
    with aioresponses() as mocked:
        mocked.post(
            "https://api.standx.com/v1/offchain/prepare-signin?chain=bsc",
            payload={"signedData": ""},
        )

        with pytest.raises(AuthenticationError, match="No signedData"):
            async with StandXHTTPClient(config) as _client:
                pass


@pytest.mark.asyncio
async def test_obtain_jwt_invalid_signed_data(config: Settings) -> None:
    """JWT取得時にsignedDataのデコードが失敗した場合のエラーを確認."""
    with aioresponses() as mocked:
        mocked.post(
            "https://api.standx.com/v1/offchain/prepare-signin?chain=bsc",
            payload={"signedData": "not-a-valid-jwt"},
        )

        with pytest.raises(AuthenticationError, match="Failed to decode signedData"):
            async with StandXHTTPClient(config) as _client:
                pass


@pytest.mark.asyncio
async def test_obtain_jwt_network_error(config: Settings) -> None:
    """JWT取得時のネットワークエラーを確認."""
    with aioresponses() as mocked:
        mocked.post(
            "https://api.standx.com/v1/offchain/prepare-signin?chain=bsc",
            exception=aiohttp.ClientError("Connection refused"),
        )

        with pytest.raises(AuthenticationError, match="Network error during JWT"):
            async with StandXHTTPClient(config) as _client:
                pass


@pytest.mark.asyncio
async def test_obtain_jwt_login_failure(config: Settings) -> None:
    """JWT取得時にloginが失敗した場合のエラーを確認."""
    import base64

    # 有効なJWT形式のsignedDataを作成
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b"=")
    payload = base64.urlsafe_b64encode(b'{"message":"test message"}').rstrip(b"=")
    sig = base64.urlsafe_b64encode(b"signature").rstrip(b"=")
    fake_jwt = f"{header.decode()}.{payload.decode()}.{sig.decode()}"

    with aioresponses() as mocked:
        mocked.post(
            "https://api.standx.com/v1/offchain/prepare-signin?chain=bsc",
            payload={"signedData": fake_jwt},
        )
        mocked.post(
            "https://api.standx.com/v1/offchain/login?chain=bsc",
            status=401,
            body="Unauthorized",
        )

        with pytest.raises(AuthenticationError, match="Failed to login"):
            async with StandXHTTPClient(config) as _client:
                pass


@pytest.mark.asyncio
async def test_obtain_jwt_no_token_in_response(config: Settings) -> None:
    """JWT取得時にレスポンスにtokenが含まれない場合のエラーを確認."""
    import base64

    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b"=")
    payload = base64.urlsafe_b64encode(b'{"message":"test message"}').rstrip(b"=")
    sig = base64.urlsafe_b64encode(b"signature").rstrip(b"=")
    fake_jwt = f"{header.decode()}.{payload.decode()}.{sig.decode()}"

    with aioresponses() as mocked:
        mocked.post(
            "https://api.standx.com/v1/offchain/prepare-signin?chain=bsc",
            payload={"signedData": fake_jwt},
        )
        mocked.post(
            "https://api.standx.com/v1/offchain/login?chain=bsc",
            payload={"token": ""},
        )

        with pytest.raises(AuthenticationError, match="No token in login"):
            async with StandXHTTPClient(config) as _client:
                pass
