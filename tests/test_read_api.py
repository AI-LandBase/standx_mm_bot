"""scripts/read_api.py のテスト."""

import sys
from pathlib import Path

import pytest
from aioresponses import aioresponses

# scripts/ はパッケージではないため、sys.pathに追加してインポート
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from read_api import BSC_RPC_URL, get_bsc_balance  # noqa: E402


@pytest.mark.asyncio
async def test_get_bsc_balance_returns_dusd() -> None:
    """get_bsc_balanceがDUSD残高を含むdictを返すことを確認."""
    with aioresponses() as mocked:
        # BNB: 1.0 BNB = 1e18 wei
        mocked.post(BSC_RPC_URL, payload={"result": "0xDE0B6B3A7640000"})
        # USDC: 100.0
        mocked.post(BSC_RPC_URL, payload={"result": "0x56BC75E2D63100000"})
        # USDT: 200.0
        mocked.post(BSC_RPC_URL, payload={"result": "0xAD78EBC5AC6200000"})
        # DUSD: 50.0
        mocked.post(BSC_RPC_URL, payload={"result": "0x2B5E3AF16B1880000"})

        result = await get_bsc_balance("0x1234567890abcdef1234567890abcdef12345678")

    assert result["bnb"] == pytest.approx(1.0)
    assert result["usdc"] == pytest.approx(100.0)
    assert result["usdt"] == pytest.approx(200.0)
    assert result["dusd"] == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_get_bsc_balance_dusd_zero() -> None:
    """DUSD残高がゼロの場合も正しく返すことを確認."""
    with aioresponses() as mocked:
        # BNB, USDC, USDT, DUSD すべて0
        for _ in range(4):
            mocked.post(BSC_RPC_URL, payload={"result": "0x0"})

        result = await get_bsc_balance("0x1234567890abcdef1234567890abcdef12345678")

    assert result["bnb"] == 0.0
    assert result["usdc"] == 0.0
    assert result["usdt"] == 0.0
    assert result["dusd"] == 0.0


@pytest.mark.asyncio
async def test_get_bsc_balance_has_all_keys() -> None:
    """get_bsc_balanceの戻り値にbnb, usdc, usdt, dusdのキーがあることを確認."""
    with aioresponses() as mocked:
        for _ in range(4):
            mocked.post(BSC_RPC_URL, payload={"result": "0x0"})

        result = await get_bsc_balance("0x1234567890abcdef1234567890abcdef12345678")

    assert set(result.keys()) == {"bnb", "usdc", "usdt", "dusd"}
