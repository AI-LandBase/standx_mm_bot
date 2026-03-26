"""データモデル定義."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Side(StrEnum):
    """注文サイド."""

    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    """注文タイプ."""

    LIMIT = "LIMIT"
    MARKET = "MARKET"


class OrderStatus(StrEnum):
    """注文ステータス."""

    OPEN = "OPEN"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELED = "CANCELED"


class Action(StrEnum):
    """約定回避アクション."""

    HOLD = "HOLD"  # 現状維持
    ESCAPE = "ESCAPE"  # 約定回避（キャンセルまたは外側に移動）
    REPOSITION = "REPOSITION"  # 再配置


@dataclass
class Order:
    """注文情報."""

    id: str
    symbol: str
    side: Side
    price: float
    size: float
    order_type: OrderType
    status: OrderStatus
    filled_size: float = 0.0
    timestamp: datetime | None = None


@dataclass
class Position:
    """ポジション情報."""

    symbol: str
    side: Side
    size: float
    entry_price: float
    unrealized_pnl: float = 0.0


@dataclass
class PriceUpdate:
    """価格更新情報."""

    symbol: str
    mark_price: float
    index_price: float
    timestamp: datetime


@dataclass
class Trade:
    """約定情報."""

    id: str
    order_id: str
    symbol: str
    side: Side
    price: float
    size: float
    fee: float
    timestamp: datetime
