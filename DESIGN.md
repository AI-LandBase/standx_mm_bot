# DESIGN.md - Bot 実装設計書

StandX MM Bot の実装設計書。アーキテクチャ、実装フェーズ、判断ロジック、パラメータ設定を定義する。

**最終更新**: 2026-04-10

---

## 📋 目次

1. [概要](#概要)
2. [アーキテクチャ](#アーキテクチャ)
3. [実装フェーズ](#実装フェーズ)
4. [データモデル](#データモデル)
5. [判断ロジック](#判断ロジック)
6. [パラメータ設定](#パラメータ設定)
7. [重要な実装ポイント](#重要な実装ポイント)
8. [API 仕様](#api-仕様)
9. [エラーハンドリング](#エラーハンドリング)
10. [テスト方針](#テスト方針)
11. [次のステップ](#次のステップ)

---

## 概要

### 目的

StandX MM Bot の実装における全体像・アーキテクチャ・実装フェーズを明確化し、開発の指針とする。

### 設計思想

```
約定 = 失敗
```

| 項目 | 方針 |
|------|------|
| 約定 | **しない**（手数料ゼロ、FR リスクゼロ） |
| 建玉 | **持たない**（清算リスクゼロ） |
| 距離 | 10bps 以内だが約定しない位置 (8bps) |
| 空白時間 | **最小化**（発注先行 or キャンセル優先、資金効率とトレードオフ） |

### 報酬条件

| 報酬プログラム | 条件 | Bot の役割 |
|----------------|------|-----------|
| **Maker Points** | mark_price ± 10bps 以内、3秒以上 | 距離を維持し続ける |
| **Maker Uptime** | 両サイド ± 10bps、毎時30分以上 | 空白時間ゼロで板に居続ける |

---

## アーキテクチャ

### 全体構成

```
┌─────────────────────────────────────────────────────────┐
│                    StandX MM Bot                        │
├─────────────────────────────────────────────────────────┤
│  __main__.py                                            │
│  - エントリーポイント                                   │
│  - シグナルハンドリング (SIGINT, SIGTERM)              │
│  - 戦略起動・終了処理                                   │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  strategy/maker.py (メイン戦略)                         │
│  - 状態管理 (現在の注文、mark_price)                   │
│  - 判断ロジック統合 (evaluate_order)                   │
│  - アクション実行 (ESCAPE, REPOSITION, HOLD)           │
│  - 厳格モード: 約定時ポジションクローズ                │
└─────────────────────────────────────────────────────────┘
         │              │              │
         ▼              ▼              ▼
┌────────────┐  ┌──────────────┐  ┌──────────────┐
│ core/      │  │ client/      │  │ config.py    │
│            │  │              │  │ models.py    │
│ - order    │  │ - http       │  │ auth.py      │
│ - escape   │  │ - websocket  │  │              │
│ - risk     │  │              │  │              │
│ - distance │  │              │  │              │
└────────────┘  └──────────────┘  └──────────────┘
```

### ファイル構成と責務

```
src/standx_mm_bot/
├── __init__.py                # パッケージ初期化
├── __main__.py                # エントリーポイント
│   └── 責務:
│       - asyncio イベントループ管理
│       - シグナルハンドリング (SIGINT, SIGTERM)
│       - MakerStrategy 起動・終了
│
├── config.py                  # 設定管理
│   └── 責務:
│       - pydantic-settings で .env から設定読み込み
│       - バリデーション（distance_bps の範囲チェック等）
│       - デフォルト値設定
│
├── models.py                  # データモデル
│   └── 責務:
│       - Order, Position, PriceUpdate 等のデータクラス
│       - Enum (Side, OrderType, OrderStatus, Action)
│       - 型安全性の確保
│
├── auth.py                    # 認証
│   └── 責務:
│       - JWT 生成 (Ed25519 署名)
│       - リクエスト署名 (timestamp, method, path, body)
│       - 認証ヘッダー生成
│
├── client/
│   ├── __init__.py
│   ├── http.py                # REST API クライアント
│   │   └── 責務:
│   │       - aiohttp.ClientSession 管理
│   │       - エンドポイント呼び出し (new_order, cancel_order, etc.)
│   │       - 認証ヘッダー付与
│   │       - レート制限対応
│   │       - エラーハンドリング
│   │
│   └── websocket.py           # WebSocket クライアント
│       └── 責務:
│           - WebSocket 接続管理
│           - チャンネル購読 (price, order, trade)
│           - 自動再接続
│           - メッセージパース・コールバック通知
│
├── core/
│   ├── __init__.py
│   ├── order.py               # 注文管理
│   │   └── 責務:
│   │       - 注文発注ロジック (ALO, post_only)
│   │       - 注文キャンセルロジック
│   │       - 注文状態管理
│   │       - 再配置ロジック (発注先行、キャンセル後)
│   │       - asyncio.Lock で注文操作の競合防止
│   │
│   ├── escape.py              # 約定回避 (約定前)
│   │   └── 責務:
│   │       - is_approaching() 判定
│   │       - escape_threshold_bps チェック
│   │       - 外側への移動 (outer_escape_distance_bps)
│   │
│   ├── risk.py                # 厳格モード (約定後)
│   │   └── 責務:
│   │       - 約定検知 (trade WebSocket)
│   │       - 即座に成行でポジションクローズ
│   │       - ポジションゼロ確認
│   │       - エラーログ出力 → Bot終了
│   │
│   └── distance.py            # bps 計算・閾値判定
│       └── 責務:
│           - calculate_distance_bps()
│           - calculate_target_price()
│           - is_approaching() (価格が接近しているか)
│           - 各種閾値チェック関数
│
└── strategy/
    ├── __init__.py
    └── maker.py               # メイン戦略
        └── 責務:
            - 状態管理 (current_orders, mark_price, position)
            - WebSocket コールバック処理
            - evaluate_order() 判断ロジック統合
            - アクション実行 (ESCAPE, REPOSITION, HOLD)
            - 厳格モード統合 (約定時の risk.py 呼び出し)
```

### モジュール間の依存関係

```
__main__.py
    ↓
strategy/maker.py
    ↓
    ├── core/order.py
    │       ↓
    │   client/http.py → auth.py
    │
    ├── core/escape.py → core/distance.py
    ├── core/risk.py → client/http.py
    ├── client/websocket.py
    └── config.py, models.py
```

**原則**:
- 上位層（strategy）→ 下位層（core, client）の一方向依存
- core 内のモジュールは相互依存を最小化
- client は auth に依存、他に依存しない
- models, config は全モジュールから利用可能（依存される側）

---

## 実装フェーズ

### Phase 0: 事前準備 (Prerequisites)

**目的**: ウォレット生成、チェーン統一、開発環境整備

**実装項目**:

1. **scripts/create_wallet.py**
   - Ed25519鍵ペア生成（PyNaCl使用）
   - .envファイル自動生成
   - セキュリティ対策（パーミッション600）
   - Solanaアドレス形式（Base58）

2. **Makefileコマンド**
   - `make wallet`: ウォレット自動生成

3. **BSC → Solana統一**
   - auth.pyはEd25519専用で実装済み
   - ウォレット生成もEd25519に統一
   - 設定のデフォルトを `solana` に変更

**受け入れ基準**:
- [x] `make wallet` でSolanaウォレット生成
- [x] .envファイルに秘密鍵とアドレスを自動書き込み
- [x] 既存.envファイルは保護（上書きしない）
- [x] パーミッション600でセキュア
- [x] auth.pyとの整合性確保（Ed25519統一）

**工数見積**: 4時間

**実装履歴**:
- Issue #14（Phase 2-1）の一部として実装完了
- BSCからSolanaへの統一も同時に実施

---

### Phase 1: 基盤 (Foundation)

**目的**: 設定管理、データモデル、認証の基盤を構築

**実装項目**:

1. **config.py**
   - `Settings` クラス (pydantic-settings)
   - 環境変数読み込み
   - デフォルト値設定
   - バリデーション

2. **models.py**
   - `Side` (BUY, SELL)
   - `OrderType` (LIMIT, MARKET)
   - `OrderStatus` (OPEN, FILLED, CANCELED)
   - `Action` (HOLD, ESCAPE, REPOSITION)
   - `Order`, `Position`, `PriceUpdate` データクラス

3. **auth.py**
   - JWT 生成関数
   - Ed25519 署名関数
   - 認証ヘッダー生成関数

**受け入れ基準**:
- [ ] Settings で全パラメータを .env から読み込める
- [ ] models で全データ型が定義されている
- [ ] auth で JWT と署名が正しく生成される
- [ ] ユニットテスト: config, models, auth

**工数見積**: 6時間

---

### Phase 2: API クライアント (Client)

**目的**: REST API と WebSocket の通信基盤を構築

**実装項目**:

1. **client/http.py**
   - `StandXHTTPClient` クラス
   - エンドポイント:
     - `get_symbol_price()`
     - `new_order()`
     - `cancel_order()`
     - `get_open_orders()`
     - `get_position()`
   - 認証ヘッダー自動付与
   - エラーハンドリング
   - レート制限対応

2. **client/websocket.py**
   - `StandXWebSocketClient` クラス
   - チャンネル購読:
     - `price` (mark_price)
     - `order` (注文状態変化)
     - `trade` (約定)
   - コールバック登録機構
   - 自動再接続
   - 認証 (order, trade チャンネル)

**受け入れ基準**:
- [x] HTTP クライアントで全エンドポイント呼び出し可能
- [ ] WebSocket で price, order, trade を受信できる
- [ ] 自動再接続が動作する
- [x] 統合テスト: REST API（モック使用）
- [ ] 統合テスト: WebSocket

**工数見積**: 10時間

**実装履歴**:
- **Phase 2-1 (client/http.py)**: Issue #14で実装完了
  - 全エンドポイント実装
  - 認証ヘッダー自動付与
  - エラーハンドリング（401, 429リトライ）
  - ドライランモード対応
  - ユニットテスト12件、統合テスト4件
  - **追加実装**: ウォレット自動生成（scripts/create_wallet.py）
  - **追加実装**: BSC→Solana統一
  - **追加実装**: API読み取りツール（scripts/read_api.py、Issue #34）
- **Phase 2-2 (client/websocket.py)**: Issue #15（未実装）

---

### Phase 3: コアロジック (Core Logic)

**目的**: 注文管理、約定回避、厳格モード、距離計算の実装

**実装項目**:

1. **core/distance.py**
   ```python
   def calculate_distance_bps(order_price: float, mark_price: float) -> float:
       """注文と mark_price の距離を bps で計算"""
       return abs(order_price - mark_price) / mark_price * 10000

   def calculate_target_price(mark_price: float, side: Side, distance_bps: float) -> float:
       """目標価格を計算"""
       offset = mark_price * (distance_bps / 10000)
       if side == Side.BUY:
           return mark_price - offset
       else:
           return mark_price + offset

   def is_approaching(mark_price: float, order_price: float, side: Side) -> bool:
       """価格が注文に接近しているか判定"""
       if side == Side.BUY:
           return mark_price < order_price  # 価格が下がっている
       else:
           return mark_price > order_price  # 価格が上がっている
   ```

2. **core/order.py**
   - `OrderManager` クラス
   - asyncio.Lock で注文操作の競合防止
   - 発注先行、キャンセル後の実装:
     ```python
     async def reposition_order(self, old_order, new_price):
         # 1. 新価格で発注
         new_order = await self.place_order(new_price)
         # 2. 確認後、旧注文キャンセル
         if new_order:
             await self.cancel_order(old_order.id)
     ```
   - ALO (Add Liquidity Only) フラグ設定

3. **core/escape.py**
   - 約定回避ロジック
   - `should_escape()` 判定
   - 外側への移動ロジック

4. **core/risk.py**
   - 厳格モード (約定後ポジションクローズ)
   - `close_position_immediately()` 実装
   - Bot終了ロジック (sys.exit)

**受け入れ基準**:
- [ ] distance.py で bps 計算が正確
- [ ] order.py で発注先行、キャンセル後が動作
- [ ] escape.py で約定回避判定が正しい
- [ ] risk.py でポジションクローズが即座に実行される
- [ ] ユニットテスト: distance, order, escape, risk

**工数見積**: 12時間

---

### Phase 4: 戦略統合 (Strategy Integration)

**目的**: メイン戦略ロジックと統合、エントリーポイント実装

**実装項目**:

1. **strategy/maker.py**
   - `MakerStrategy` クラス
   - 状態管理:
     ```python
     class MakerStrategy:
         def __init__(self):
             self.mark_price: float = 0.0
             self.bid_order: Optional[Order] = None
             self.ask_order: Optional[Order] = None
             self.position: Optional[Position] = None
             self.order_manager = OrderManager()
             self.escape_logic = EscapeLogic()
             self.risk_manager = RiskManager()
     ```
   - WebSocket コールバック:
     - `on_price_update()` → 判断ロジック呼び出し
     - `on_order_update()` → 注文状態更新
     - `on_trade()` → 厳格モード発動
   - 判断ロジック統合:
     ```python
     async def evaluate_and_act(self, order: Order, side: Side):
         action = self.evaluate_order(order, self.mark_price, side)

         if action == Action.ESCAPE:
             await self.escape_logic.escape_order(order, self.mark_price, side)
         elif action == Action.REPOSITION:
             new_price = calculate_target_price(self.mark_price, side, TARGET_DISTANCE_BPS)
             await self.order_manager.reposition_order(order, new_price)
         # HOLD: 何もしない
     ```
   - 厳格モード統合:
     ```python
     async def on_trade(self, trade_data):
         if trade_data["my_trade"]:
             # 即座にポジションクローズ
             await self.risk_manager.close_position_immediately()
             # Bot終了（約定 = 失敗）
             logger.error("Bot stopped due to trade execution.")
             sys.exit(1)
     ```

2. **__main__.py**
   - エントリーポイント
   - シグナルハンドリング:
     ```python
     import signal
     import asyncio

     async def main():
         strategy = MakerStrategy()

         loop = asyncio.get_running_loop()
         for sig in (signal.SIGINT, signal.SIGTERM):
             loop.add_signal_handler(sig, lambda: asyncio.create_task(strategy.shutdown()))

         await strategy.run()

     if __name__ == "__main__":
         asyncio.run(main())
     ```

**受け入れ基準**:
- [ ] MakerStrategy で全ロジックが統合されている
- [ ] WebSocket コールバックが正しく動作
- [ ] 判断ロジックが期待通りに実行される
- [ ] 厳格モードが約定時に発動する
- [ ] SIGINT, SIGTERM で正常終了する
- [ ] 統合テスト: 戦略全体

**工数見積**: 10時間

---

### Phase 5: テスト・ドキュメント (Testing & Documentation)

**目的**: テスト網羅、ドキュメント整備

**実装項目**:

1. **ユニットテスト**
   - `tests/test_auth.py`
   - `tests/test_distance.py`
   - `tests/test_order.py`
   - `tests/test_escape.py`
   - `tests/test_risk.py`

2. **統合テスト**
   - `tests/test_integration.py`
   - WebSocket モック
   - REST API モック
   - 戦略全体のシナリオテスト

3. **ドキュメント更新**
   - README.md の更新
   - CONTRIBUTING.md の更新
   - CLAUDE.md の更新

**受け入れ基準**:
- [ ] テストカバレッジ 80% 以上
- [ ] 全統合テストが成功
- [ ] ドキュメントが最新

**工数見積**: 8時間

---

### フェーズ間の依存関係

```
Phase 0 (事前準備)
    ↓
Phase 1 (基盤)
    ↓
Phase 2 (API クライアント)
    ↓
Phase 3 (コアロジック)
    ↓
Phase 4 (戦略統合)
    ↓
Phase 5 (テスト・ドキュメント)
```

**合計見積**: 46時間 (5.8営業日)
**バッファ (+25%)**: 57.5時間 (7.2営業日)

---

## データモデル

### Enum 定義

```python
from enum import Enum

class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"

class OrderStatus(str, Enum):
    OPEN = "OPEN"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELED = "CANCELED"

class Action(str, Enum):
    HOLD = "HOLD"              # 何もしない
    ESCAPE = "ESCAPE"          # 約定回避（外側に移動またはキャンセル）
    REPOSITION = "REPOSITION"  # 再配置（目標位置に移動）
```

### データクラス

```python
from dataclasses import dataclass
from typing import Optional
from datetime import datetime

@dataclass
class Order:
    """注文"""
    id: str
    symbol: str
    side: Side
    price: float
    size: float
    order_type: OrderType
    status: OrderStatus
    filled_size: float = 0.0
    timestamp: datetime = None

@dataclass
class Position:
    """ポジション"""
    symbol: str
    side: Side
    size: float
    entry_price: float
    unrealized_pnl: float = 0.0

@dataclass
class PriceUpdate:
    """価格更新"""
    symbol: str
    mark_price: float
    index_price: float
    timestamp: datetime

@dataclass
class Trade:
    """約定"""
    id: str
    order_id: str
    symbol: str
    side: Side
    price: float
    size: float
    fee: float
    timestamp: datetime
```

---

## 判断ロジック

### evaluate_order() の詳細仕様

```python
from core.distance import (
    calculate_distance_bps,
    calculate_target_price,
    is_approaching
)
from models import Action, Side

def evaluate_order(
    order: Order,
    mark_price: float,
    side: Side,
    config: Settings
) -> Action:
    """
    注文の状態を評価し、実行すべきアクションを決定

    Args:
        order: 評価対象の注文
        mark_price: 現在の mark_price
        side: 注文サイド (BUY or SELL)
        config: 設定 (閾値パラメータ)

    Returns:
        Action: 実行すべきアクション (HOLD, ESCAPE, REPOSITION)
    """
    distance = calculate_distance_bps(order.price, mark_price)

    # 優先順位1: 約定回避 (ESCAPE)
    # 価格が接近している場合のみチェック
    if is_approaching(mark_price, order.price, side):
        if distance < config.escape_threshold_bps:  # デフォルト: 3bps
            return Action.ESCAPE

    # 優先順位2: 10bps 境界への接近 (REPOSITION)
    # 10bps - reposition_threshold_bps = 8bps より外側にいる場合
    if distance > (10 - config.reposition_threshold_bps):  # デフォルト: 8bps
        return Action.REPOSITION

    # 優先順位3: 目標価格からの乖離 (REPOSITION)
    # 価格変動により目標位置から離れた場合
    target_price = calculate_target_price(
        mark_price,
        side,
        config.target_distance_bps  # デフォルト: 8bps
    )

    price_diff_bps = abs(order.price - target_price) / mark_price * 10000
    if price_diff_bps > config.price_move_threshold_bps:  # デフォルト: 5bps
        return Action.REPOSITION

    # それ以外: 保持
    return Action.HOLD
```

### 判断ロジックのフローチャート

```
価格更新を受信
    ↓
両サイドの注文を evaluate_order()
    ↓
    ├─ 価格が接近 & 距離 < 3bps
    │       ↓
    │   【ESCAPE】外側 (15bps) に移動
    │
    ├─ 距離 > 8bps (10bps 境界に接近)
    │       ↓
    │   【REPOSITION】目標位置 (8bps) に移動
    │
    ├─ 目標価格からの乖離 > 5bps
    │       ↓
    │   【REPOSITION】目標位置 (8bps) に移動
    │
    └─ それ以外
            ↓
        【HOLD】何もしない
```

### Action 実行ロジック

#### ESCAPE (約定回避)

```python
async def execute_escape(order: Order, mark_price: float, side: Side, config: Settings):
    """
    約定回避: 外側に移動
    """
    outer_price = calculate_target_price(
        mark_price,
        side,
        config.outer_escape_distance_bps  # デフォルト: 15bps
    )

    # 発注先行、キャンセル後
    new_order = await order_manager.place_order(
        side=side,
        price=outer_price,
        size=config.order_size
    )

    if new_order:
        await order_manager.cancel_order(order.id)

    logger.warning(f"ESCAPE: {side} order moved to {outer_price} ({config.outer_escape_distance_bps}bps)")
```

#### REPOSITION (再配置)

```python
async def execute_reposition(order: Order, mark_price: float, side: Side, config: Settings):
    """
    再配置: 目標位置に移動
    """
    target_price = calculate_target_price(
        mark_price,
        side,
        config.target_distance_bps  # デフォルト: 8bps
    )

    # 発注先行、キャンセル後
    new_order = await order_manager.place_order(
        side=side,
        price=target_price,
        size=config.order_size
    )

    if new_order:
        await order_manager.cancel_order(order.id)

    logger.info(f"REPOSITION: {side} order moved to {target_price} ({config.target_distance_bps}bps)")
```

#### HOLD (保持)

```python
async def execute_hold():
    """
    保持: 何もしない
    """
    pass  # ログも出さない（ノイズ削減）
```

---

## パラメータ設定

### 環境変数一覧

| パラメータ | 環境変数 | デフォルト | 説明 |
|-----------|----------|-----------|------|
| **認証** | | | |
| private_key | `STANDX_PRIVATE_KEY` | (必須) | ウォレット秘密鍵 |
| wallet_address | `STANDX_WALLET_ADDRESS` | (必須) | ウォレットアドレス |
| chain | `STANDX_CHAIN` | `solana` | チェーン (solana/bsc) |
| **取引設定** | | | |
| symbol | `SYMBOL` | `ETH_USDC` | 取引ペア |
| order_size | `ORDER_SIZE` | `0.1` | 片側注文サイズ |
| **距離設定** | | | |
| target_distance_bps | `TARGET_DISTANCE_BPS` | `8` | 目標距離 (bps) |
| escape_threshold_bps | `ESCAPE_THRESHOLD_BPS` | `3` | 約定回避距離 (bps) |
| outer_escape_distance_bps | `OUTER_ESCAPE_DISTANCE_BPS` | `15` | 逃げる先の距離 (bps) |
| reposition_threshold_bps | `REPOSITION_THRESHOLD_BPS` | `2` | 10bps 境界への接近しきい値 (bps) |
| price_move_threshold_bps | `PRICE_MOVE_THRESHOLD_BPS` | `5` | 価格変動による再配置しきい値 (bps) |
| **接続設定** | | | |
| ws_reconnect_interval | `WS_RECONNECT_INTERVAL` | `5000` | WebSocket 再接続間隔 (ms) |
| jwt_expires_seconds | `JWT_EXPIRES_SECONDS` | `604800` | JWT 有効期限 (秒, 7日) |

### config.py 実装例

```python
from pydantic_settings import BaseSettings
from pydantic import Field, field_validator

class Settings(BaseSettings):
    """Bot 設定"""

    # 認証
    standx_private_key: str = Field(..., description="ウォレット秘密鍵（Ed25519、hex形式）")
    standx_wallet_address: str = Field(..., description="ウォレットアドレス（Solana: Base58形式）")
    standx_chain: str = Field("solana", description="チェーン (solana/bsc)")

    # 取引設定
    symbol: str = Field("ETH_USDC", description="取引ペア")
    order_size: float = Field(0.1, description="片側注文サイズ")

    # 距離設定
    target_distance_bps: float = Field(8.0, description="目標距離 (bps)")
    escape_threshold_bps: float = Field(3.0, description="約定回避距離 (bps)")
    outer_escape_distance_bps: float = Field(15.0, description="逃げる先の距離 (bps)")
    reposition_threshold_bps: float = Field(2.0, description="10bps 境界への接近しきい値")
    price_move_threshold_bps: float = Field(5.0, description="価格変動による再配置しきい値")

    # 接続設定
    ws_reconnect_interval: int = Field(5000, description="WebSocket 再接続間隔 (ms)")
    jwt_expires_seconds: int = Field(604800, description="JWT 有効期限 (秒)")

    @field_validator("target_distance_bps")
    def validate_target_distance(cls, v):
        if not 0 < v < 10:
            raise ValueError("target_distance_bps must be between 0 and 10")
        return v

    @field_validator("escape_threshold_bps")
    def validate_escape_threshold(cls, v):
        if v <= 0:
            raise ValueError("escape_threshold_bps must be positive")
        return v

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
```

### パラメータチューニングガイド

| パラメータ | 増やすと | 減らすと |
|-----------|---------|---------|
| `target_distance_bps` | 約定リスク減、報酬倍率減 | 報酬倍率増、約定リスク増 |
| `escape_threshold_bps` | 逃げが遅い、約定リスク増 | 逃げが早い、再配置頻度増 |
| `outer_escape_distance_bps` | 逃げ先が遠い、戻りに時間 | 逃げ先が近い、再度逃げる可能性 |
| `reposition_threshold_bps` | 10bps 超えリスク減、頻度増 | 10bps 超えリスク増、頻度減 |
| `price_move_threshold_bps` | 価格追従が鈍い、逸脱許容 | 価格追従が敏感、再配置頻度増 |

**推奨設定**（デフォルト値）は、報酬最大化と約定回避のバランスを取っている。

---

## 重要な実装ポイント

### 1. ALO (Add Liquidity Only) の使用

**目的**: テイカーになることを防止

```python
# 注文発注時に ALO フラグを設定
order_params = {
    "symbol": "ETH_USDC",
    "side": "BUY",
    "price": target_price,
    "size": order_size,
    "order_type": "LIMIT",
    "post_only": True,  # ← ALO フラグ
}
```

**効果**:
- 即座に約定する価格で注文を出した場合、注文は拒否される
- テイカー手数料を回避
- 意図しない約定を防止

**注意**:
- StandX API のドキュメントで `post_only` または `add_liquidity_only` のフィールド名を確認する

---

### 2. 発注先行 vs キャンセル優先（資金効率とのトレードオフ）

注文の更新（価格変更）には2つの方式があり、それぞれメリット・デメリットがある。

#### 方式①：発注先行（デフォルト推奨）

**実装**:

```python
# ✅ 発注 → キャンセル（空白時間ゼロ）
new_order = await place_order(new_price)
if new_order:
    await cancel_order(old_order.id)
```

**メリット**:
- **空白時間ゼロ**：板から消える瞬間がない
- **Maker Uptime 最大化**：「毎時間30分以上、両サイド存在」条件を確実に満たす
- 報酬獲得の機会損失なし

**デメリット**:
- **一時的に注文が2本になる**：例えばロング2本、ショート1本の状態が発生
- **必要証拠金が1.5倍必要**：片側の注文サイズ分だけ余分な証拠金が必要
- 資金効率が悪い

**適用場面**:
- 資金が十分にある場合
- Maker Uptime を最大化したい場合
- 空白時間による報酬損失を避けたい場合

---

#### 方式②：キャンセル優先（資金効率重視）

**実装**:

```python
# キャンセル → 発注（空白時間が発生）
await cancel_order(old_order.id)
new_order = await place_order(new_price)
```

**メリット**:
- **必要証拠金が少ない**：常に片側1本のみで済む
- **資金効率が良い**：少ない資金で運用可能
- 証拠金維持率が高い（安全性向上）

**デメリット**:
- **空白時間が発生**：キャンセル～発注の間（通常100-300ms程度）板から消える
- **Maker Uptime への影響**：空白時間が積み重なると毎時30分の条件を満たせない可能性
- 報酬獲得の機会損失

**適用場面**:
- 資金が限られている場合
- 証拠金維持率を高く保ちたい場合
- Uptime よりも約定回避を優先する場合

---

#### 推奨設定

| 資金状況 | 推奨方式 | 理由 |
|---------|---------|------|
| **資金が十分** | ①発注先行 | Maker Uptime 最大化、報酬最大化 |
| **資金が限られている** | ②キャンセル優先 | 資金効率重視、安全性向上 |
| **証拠金維持率が低い** | ②キャンセル優先 | 清算リスク回避 |

---

#### 実装オプション（将来の拡張）

設定ファイルで方式を選択可能にする:

```python
# config.py
order_update_strategy: str = Field(
    "place_first",
    description="注文更新戦略: place_first（発注先行） / cancel_first（キャンセル優先）"
)
```

```python
# core/order.py
async def reposition_order(self, order, new_price):
    if self.config.order_update_strategy == "place_first":
        # 発注先行
        new_order = await self.place_order(new_price)
        if new_order:
            await self.cancel_order(order.id)
    else:
        # キャンセル優先
        await self.cancel_order(order.id)
        new_order = await self.place_order(new_price)
```

**注意**:
- Phase 1-3 では「発注先行」のみ実装
- 設定オプションは Phase 4 以降で検討

---

### 3. asyncio.Lock で注文操作の競合防止

**問題**:

```python
# WebSocket コールバックは並行実行される
async def on_price_update(price):
    await reposition_order()  # ← 同時に複数回呼ばれる可能性

async def on_order_update(order):
    await reposition_order()  # ← 競合
```

**解決**:

```python
class OrderManager:
    def __init__(self):
        self._lock = asyncio.Lock()

    async def reposition_order(self, order, new_price):
        async with self._lock:
            # ロック内で注文操作
            new_order = await self.place_order(new_price)
            if new_order:
                await self.cancel_order(order.id)
```

**効果**:
- 注文操作が逐次実行される
- 重複発注を防止
- 注文状態の整合性を保つ

---

### 4. 厳格モード: 約定時の即座クローズ

**実装**:

```python
async def on_trade(self, trade_data):
    """
    約定検知時の処理（厳格モード）

    約定 = 失敗
    → ポジションクローズ → ログ出力 → Bot終了
    """
    if not trade_data.get("my_trade"):
        return  # 自分の約定でない場合は無視

    logger.error(f"CRITICAL: Trade executed! Design failure detected: {trade_data}")

    # 何よりも先にポジションクローズ
    await self.risk_manager.close_position_immediately()

    # ポジションゼロ確認
    position = await self.http_client.get_position(self.config.symbol)
    if position and position.size > 0:
        logger.error(f"Position still exists: {position}")
        # リトライロジック
        await self.risk_manager.close_position_immediately()

    # Bot終了（自動復帰しない）
    logger.error(
        "Bot stopped due to trade execution. "
        "Check parameters (ESCAPE_THRESHOLD_BPS, etc.) and restart manually."
    )
    sys.exit(1)
```

**close_position_immediately() の実装**:

```python
async def close_position_immediately(self):
    """
    成行でポジションを即座にクローズ
    """
    position = await self.http_client.get_position(self.config.symbol)

    if not position or position.size == 0:
        return  # ポジションなし

    # 成行で反対売買
    close_side = Side.SELL if position.side == Side.BUY else Side.BUY

    await self.http_client.new_order(
        symbol=self.config.symbol,
        side=close_side,
        size=position.size,
        order_type=OrderType.MARKET  # ← 成行
    )

    logger.info(f"Position closed: {position}")
```

**注意**:
- 成行注文は手数料が発生する（テイカー手数料）
- しかし建玉リスク（FR、清算）を避けるために必要
- ポジションクローズ後、Bot は終了する（約定 = 失敗）

---

### 5. Ed25519 署名 (リクエスト認証)

**StandX API 認証仕様**:

1. JWT 生成 (Ed25519 秘密鍵で署名)
2. 各リクエストに署名ヘッダー付与

**auth.py 実装**:

```python
import time
import json
from nacl.signing import SigningKey
from nacl.encoding import HexEncoder
import jwt

def generate_jwt(private_key: str, wallet_address: str, chain: str, expires_seconds: int = 604800) -> str:
    """
    JWT 生成
    """
    signing_key = SigningKey(private_key, encoder=HexEncoder)

    payload = {
        "wallet_address": wallet_address,
        "chain": chain,
        "exp": int(time.time()) + expires_seconds
    }

    token = jwt.encode(payload, signing_key.encode(), algorithm="EdDSA")
    return token

def sign_request(private_key: str, method: str, path: str, body: dict = None) -> dict:
    """
    リクエスト署名

    Returns:
        dict: 署名ヘッダー
    """
    signing_key = SigningKey(private_key, encoder=HexEncoder)

    timestamp = str(int(time.time() * 1000))

    # 署名対象: timestamp + method + path + body
    message = timestamp + method.upper() + path
    if body:
        message += json.dumps(body, separators=(',', ':'))

    signature = signing_key.sign(message.encode()).signature.hex()

    return {
        "X-Standx-Timestamp": timestamp,
        "X-Standx-Signature": signature
    }
```

**使用例**:

```python
# JWT 生成（初回のみ）
jwt_token = generate_jwt(
    private_key=config.standx_private_key,
    wallet_address=config.standx_wallet_address,
    chain=config.standx_chain
)

# リクエスト署名（毎回）
async def new_order(self, symbol, side, price, size):
    path = "/api/new_order"
    body = {"symbol": symbol, "side": side, "price": price, "size": size}

    signature_headers = sign_request(
        private_key=self.config.standx_private_key,
        method="POST",
        path=path,
        body=body
    )

    headers = {
        "Authorization": f"Bearer {self.jwt_token}",
        **signature_headers
    }

    async with self.session.post(self.base_url + path, json=body, headers=headers) as resp:
        return await resp.json()
```

---

## API 仕様

### Base URL

```
REST: https://perps.standx.com
WebSocket: wss://perps.standx.com/ws-stream/v1
```

### REST API エンドポイント

#### 1. 価格取得

```
GET /api/query_symbol_price?symbol=ETH_USDC
```

**レスポンス**:

```json
{
  "symbol": "ETH_USDC",
  "mark_price": 2500.5,
  "index_price": 2500.3
}
```

#### 2. 注文発注

```
POST /api/new_order
```

**リクエスト**:

```json
{
  "symbol": "ETH_USDC",
  "side": "BUY",
  "price": 2490.0,
  "size": 0.1,
  "order_type": "LIMIT",
  "post_only": true
}
```

**レスポンス**:

```json
{
  "order_id": "order_12345",
  "status": "OPEN",
  "symbol": "ETH_USDC",
  "side": "BUY",
  "price": 2490.0,
  "size": 0.1
}
```

#### 3. 注文キャンセル

```
POST /api/cancel_order
```

**リクエスト**:

```json
{
  "order_id": "order_12345",
  "symbol": "ETH_USDC"
}
```

**レスポンス**:

```json
{
  "order_id": "order_12345",
  "status": "CANCELED"
}
```

#### 4. 未決注文一覧

```
GET /api/query_open_orders?symbol=ETH_USDC
```

**レスポンス**:

```json
{
  "orders": [
    {
      "order_id": "order_12345",
      "symbol": "ETH_USDC",
      "side": "BUY",
      "price": 2490.0,
      "size": 0.1,
      "status": "OPEN"
    }
  ]
}
```

#### 5. ポジション取得

```
GET /api/query_position?symbol=ETH_USDC
```

**レスポンス**:

```json
{
  "symbol": "ETH_USDC",
  "side": "LONG",
  "size": 0.1,
  "entry_price": 2490.0,
  "unrealized_pnl": 1.05
}
```

---

### WebSocket チャンネル

#### 1. price チャンネル (mark_price)

**購読**:

```json
{
  "method": "SUBSCRIBE",
  "params": ["price@ETH_USDC"]
}
```

**メッセージ**:

```json
{
  "channel": "price",
  "symbol": "ETH_USDC",
  "mark_price": 2500.5,
  "index_price": 2500.3,
  "timestamp": 1700000000000
}
```

#### 2. order チャンネル (注文状態変化)

**購読** (認証必要):

```json
{
  "method": "SUBSCRIBE",
  "params": ["order"],
  "auth_token": "Bearer <JWT>"
}
```

**メッセージ**:

```json
{
  "channel": "order",
  "order_id": "order_12345",
  "symbol": "ETH_USDC",
  "status": "FILLED",
  "filled_size": 0.1,
  "timestamp": 1700000000000
}
```

#### 3. trade チャンネル (約定)

**購読** (認証必要):

```json
{
  "method": "SUBSCRIBE",
  "params": ["trade"],
  "auth_token": "Bearer <JWT>"
}
```

**メッセージ**:

```json
{
  "channel": "trade",
  "trade_id": "trade_67890",
  "order_id": "order_12345",
  "symbol": "ETH_USDC",
  "side": "BUY",
  "price": 2490.0,
  "size": 0.1,
  "fee": 0.025,
  "timestamp": 1700000000000,
  "my_trade": true
}
```

---

## エラーハンドリング

### HTTP エラー

```python
class StandXHTTPClient:
    async def _request(self, method, path, **kwargs):
        try:
            async with self.session.request(method, self.base_url + path, **kwargs) as resp:
                if resp.status == 200:
                    return await resp.json()
                elif resp.status == 401:
                    logger.error("Authentication failed")
                    raise AuthenticationError("JWT expired or invalid")
                elif resp.status == 429:
                    logger.warning("Rate limit exceeded, retrying...")
                    await asyncio.sleep(1)
                    return await self._request(method, path, **kwargs)  # リトライ
                else:
                    error_body = await resp.text()
                    logger.error(f"HTTP {resp.status}: {error_body}")
                    raise APIError(f"HTTP {resp.status}: {error_body}")
        except aiohttp.ClientError as e:
            logger.error(f"Network error: {e}")
            raise NetworkError(str(e))
```

### WebSocket エラー

```python
class StandXWebSocketClient:
    async def connect(self):
        while True:
            try:
                async with websockets.connect(self.ws_url) as ws:
                    await self._subscribe_channels(ws)
                    await self._receive_messages(ws)
            except websockets.ConnectionClosed:
                logger.warning("WebSocket disconnected, reconnecting...")
                await asyncio.sleep(self.reconnect_interval / 1000)
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                await asyncio.sleep(self.reconnect_interval / 1000)
```

### 注文エラー

```python
async def place_order(self, side, price, size):
    try:
        result = await self.http_client.new_order(
            symbol=self.config.symbol,
            side=side,
            price=price,
            size=size
        )
        return result
    except APIError as e:
        if "insufficient balance" in str(e).lower():
            logger.error("Insufficient balance")
            return None
        elif "post_only" in str(e).lower():
            logger.warning("Order would be filled immediately (post_only rejected)")
            return None
        else:
            logger.error(f"Order failed: {e}")
            return None
```

---

## テスト方針

### ユニットテスト

**対象**:
- `auth.py`: JWT 生成、署名生成
- `distance.py`: bps 計算、is_approaching 判定
- `models.py`: データクラスのバリデーション
- `config.py`: 設定読み込み、バリデーション

**例**: `tests/test_distance.py`

```python
import pytest
from standx_mm_bot.core.distance import calculate_distance_bps, calculate_target_price, is_approaching
from standx_mm_bot.models import Side

def test_calculate_distance_bps():
    assert calculate_distance_bps(2490.0, 2500.0) == pytest.approx(40.0, rel=1e-2)  # 10 / 2500 * 10000 = 40bps

def test_calculate_target_price_buy():
    target = calculate_target_price(2500.0, Side.BUY, 8.0)
    assert target == pytest.approx(2498.0, rel=1e-2)  # 2500 - (2500 * 0.0008)

def test_is_approaching_buy():
    assert is_approaching(2499.0, 2498.0, Side.BUY) == True  # 価格が下がっている
    assert is_approaching(2501.0, 2498.0, Side.BUY) == False  # 価格が上がっている
```

---

### 統合テスト

**対象**:
- `client/http.py`: REST API 呼び出し (モック)
- `client/websocket.py`: WebSocket 接続 (モック)
- `strategy/maker.py`: 戦略全体のシナリオテスト

**例**: `tests/test_integration.py`

```python
import pytest
from unittest.mock import AsyncMock, patch
from standx_mm_bot.strategy.maker import MakerStrategy

@pytest.mark.asyncio
async def test_escape_scenario():
    """
    価格接近時の ESCAPE シナリオ
    """
    strategy = MakerStrategy()
    strategy.mark_price = 2500.0
    strategy.bid_order = Order(id="bid_1", price=2498.0, side=Side.BUY, ...)

    # モック: 価格が急降下
    with patch.object(strategy.order_manager, 'place_order', new_callable=AsyncMock) as mock_place:
        with patch.object(strategy.order_manager, 'cancel_order', new_callable=AsyncMock) as mock_cancel:
            # 価格更新: 2500 → 2499 (接近)
            await strategy.on_price_update({"mark_price": 2499.0})

            # ESCAPE 発動を確認
            mock_place.assert_called_once()  # 外側に発注
            mock_cancel.assert_called_once()  # 旧注文キャンセル
```

---

### テストカバレッジ目標

| モジュール | 目標カバレッジ |
|-----------|---------------|
| auth.py | 90% |
| distance.py | 95% |
| models.py | 80% |
| config.py | 80% |
| order.py | 85% |
| escape.py | 85% |
| risk.py | 85% |
| maker.py | 75% |
| **全体** | **80%** |

---

## 次のステップ

### 実装の進め方

1. **Phase 1 を実装** (config, models, auth)
   - `feature/6-phase1-foundation` ブランチ作成
   - 実装・テスト
   - PR 作成・マージ

2. **Phase 2 を実装** (client/http, client/websocket)
   - `feature/7-phase2-client` ブランチ作成
   - 実装・テスト
   - PR 作成・マージ

3. **Phase 3 を実装** (core/distance, order, escape, risk)
   - `feature/8-phase3-core` ブランチ作成
   - 実装・テスト
   - PR 作成・マージ

4. **Phase 4 を実装** (strategy/maker, __main__)
   - `feature/9-phase4-strategy` ブランチ作成
   - 実装・テスト
   - PR 作成・マージ

5. **Phase 5 を実装** (テスト・ドキュメント)
   - `feature/10-phase5-test-docs` ブランチ作成
   - カバレッジ確認
   - ドキュメント更新
   - PR 作成・マージ

### 実装中の注意事項

- 各フェーズ完了後、DESIGN.md を更新（実装中の気づきを反映）
- テストを先に書く（TDD）または同時に書く
- コミットメッセージは Conventional Commits に従う
- PR は小さく保つ（1フェーズ = 1 PR）

### フィードバックループ

```
実装 → テスト → レビュー → DESIGN.md 更新 → 次フェーズ
```

実装中に発見した問題点や改善点は、DESIGN.md にフィードバックする。

---

## 参考資料

- [GUIDE.md](./GUIDE.md) - 理論的背景・技術基礎・設計思想の教科書的解説
- [README.md](./README.md) - プロジェクト概要、クイックスタート
- [CONTRIBUTING.md](./CONTRIBUTING.md) - 開発規約・ワークフロー
- [CLAUDE.md](./CLAUDE.md) - AI 向けクイックリファレンス
- [StandX API Docs](https://docs.standx.com/standx-api/standx-api)
- [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
- [aiohttp](https://docs.aiohttp.org/)
- [websockets](https://websockets.readthedocs.io/)
- [pytest-asyncio](https://pytest-asyncio.readthedocs.io/)

---

**Last Updated**: 2026-04-10
**Version**: 1.0.0
