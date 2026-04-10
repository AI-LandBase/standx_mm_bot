# StandX MM Bot 学習ガイド

このドキュメントは、StandX MM Bot の開発を理解するための教科書的ガイドです。理論的背景から技術基礎、設計思想、数学的モデルまでを体系的に解説します。

---

## はじめに

### このドキュメントの目的

このガイドは、**「なぜこの設計なのか（Why）」** を理解することを重視しています。実務的なリファレンスは他のドキュメントに譲り、ここでは理論と根拠を深く掘り下げます。

### 前提知識レベル別の読み方

**初心者（Python基礎のみ）**
- Part 1 → Part 2 → Part 5 の順に読み進める
- Part 3, 4 は必要に応じて参照

**中級者（asyncio経験あり）**
- Part 1 → Part 3 → Part 4 の順で設計思想と数学モデルを重点的に学ぶ
- Part 2 は復習として活用

**経験者（MM Bot開発経験あり）**
- Part 1.3, Part 3, Part 4 で StandX 固有の設計判断を理解

### 既存ドキュメントとの役割分担

| ドキュメント | 役割 |
|-------------|------|
| **README.md** | プロジェクト概要、クイックスタート |
| **CONTRIBUTING.md** | 開発規約、ワークフロー |
| **CLAUDE.md** | AI 向けクイックリファレンス |
| **GUIDE.md** | 理論的背景と教育的解説（このドキュメント） |

---

## Part 1: 理論的背景

### 1.1 マーケットメイキングとは

#### 定義

マーケットメイキング（Market Making）とは、**市場に流動性を提供すること**です。具体的には、買い注文（Bid）と売り注文（Ask）の両方を常時板に置き、他のトレーダーが即座に取引できる環境を作ります。

#### 一般的なマーケットメイキングの目的

従来のマーケットメイカーは、**ビッド・アスク・スプレッド（買値と売値の差）** から利益を得ます：

```
買値: $3000.00
売値: $3000.50
スプレッド: $0.50 → これがマーケットメイカーの利益
```

しかし、このモデルには以下のリスクがあります：

- **在庫リスク**: 片側だけ約定すると建玉（ポジション）を持つことになる
- **価格変動リスク**: 建玉を持った状態で価格が逆行すると損失
- **清算リスク**: レバレッジ取引では強制決済のリスク

#### StandX の革新的アプローチ

StandX は、**約定させなくても報酬が出る**という画期的な仕組みを導入しました。これにより：

- ✅ 在庫リスクゼロ
- ✅ 価格変動リスクゼロ
- ✅ 清算リスクゼロ

**これが「約定 = 失敗」という設計思想の出発点です。**

---

### 1.2 StandXの報酬メカニズム詳解

StandX には2つの報酬プログラムがあります。

#### Maker Points（メイカーポイント）

**条件**: 板上に **3秒以上**、mark_price ± 10bps 以内に存在

**報酬倍率**:

| 距離帯 | 倍率 | 例（mark_price = $3000） |
|--------|------|------------------------|
| 0–10 bps | 100% | $2997.00 〜 $3003.00 |
| 10–30 bps | 50% | $2991.00 〜 $2997.00 / $3003.00 〜 $3009.00 |
| 30–100 bps | 10% | $2970.00 〜 $2991.00 / $3009.00 〜 $3030.00 |

**計算式**:

```
points = notional × multiplier × time / 86400
```

**重要**: 約定は不要。板に**居続ける**ことが報酬条件。

#### Maker Uptime（メイカーアップタイム）

**条件**: 毎時間 **30分以上**、**両サイド**（Bid + Ask）が mark_price ± 10bps 以内に存在

**Tier（ティア）**:

| Tier | 稼働率 | 倍率 |
|------|--------|------|
| **Boosted** | 70%以上 | 1.0x |
| **Standard** | 50%以上 | 0.5x |

**Bot の役割**: **空白時間ゼロ**で板に居続け、70%以上の稼働率を達成する

---

### 1.3 「約定 = 失敗」の設計思想

#### なぜ約定を避けるのか

StandX の報酬モデルでは、**約定は何のメリットもありません**：

| シナリオ | 従来のMM | StandX MM Bot |
|---------|---------|--------------|
| 約定なし | ❌ 利益ゼロ | ✅ Points + Uptime 獲得 |
| 約定あり | ✅ スプレッド利益 | ❌ 建玉リスク発生、報酬変わらず |

**結論**: 約定は**デメリットのみ**。徹底的に避けるべき。

#### 約定回避の3原則

**原則1: 価格が近づいたら即座に逃げる**

```python
if distance_to_order < ESCAPE_THRESHOLD_BPS:  # 3bps
    await cancel_order(order_id)
```

**原則2: 10bps 境界を超えないように再配置**

```python
if distance_to_mark > 10:  # 10bps を超えそう
    await reposition_order(target=8bps)
```

**原則3: 空白時間を最小化**

```python
# ✅ GOOD: 新規発注 → 確認 → 旧注文キャンセル
new_order = await place_order(new_price)
if new_order.status == "OPEN":
    await cancel_order(old_order_id)

# ❌ BAD: キャンセル → 発注（板から消える時間が発生）
await cancel_order(old_order_id)
await place_order(new_price)
```

#### 厳格モード（Strict Mode）

現実的には、急激な価格変動や遅延により、約定を完全に避けることは不可能です。

**対処法**: 約定検知 → **即成行でクローズ** → **Bot終了**

```python
if order.status == "FILLED":
    # 最優先: ポジションをゼロに
    await close_position_immediately()

    # 約定 = 失敗 → Bot終了
    logger.error("Bot stopped due to trade execution.")
    sys.exit(1)
```

**トレードオフ**:
- ✅ 建玉リスクをゼロに維持
- ✅ 連続約定を防止
- ❌ 手数料が発生（テイカー手数料）
- ❌ 人間の介入が必要（パラメータ見直し）

しかし、建玉を放置するリスク（FR、清算）に比べれば、手数料は許容範囲内です。

---

## Part 2: 技術基礎

### 2.1 Python asyncio の動作原理

#### なぜ asyncio を使うのか

MM Bot は以下を**並行処理**する必要があります：

1. WebSocket で価格・注文状態をリアルタイム監視
2. REST API で注文発注・キャンセル
3. 複数の判断ロジックを同時実行

**asyncio** は、1つのスレッドで複数のタスクを効率的に処理する仕組みです。

#### イベントループの基本

```python
import asyncio

async def monitor_price():
    while True:
        price = await fetch_price()
        print(f"Price: {price}")
        await asyncio.sleep(1)

async def manage_orders():
    while True:
        await check_and_update_orders()
        await asyncio.sleep(0.5)

async def main():
    # 2つのタスクを並行実行
    await asyncio.gather(
        monitor_price(),
        manage_orders()
    )

asyncio.run(main())
```

**重要な概念**:

- `async def`: 非同期関数を定義
- `await`: 他のタスクに制御を譲る（ブロッキングしない）
- `asyncio.gather()`: 複数のタスクを並行実行

#### 同期処理と非同期処理の違い

**同期処理（Blocking）**:

```python
# BAD: 1つ終わるまで次に進めない
price = requests.get("/api/price")  # 100ms待つ
orders = requests.get("/api/orders")  # 100ms待つ
# 合計: 200ms
```

**非同期処理（Non-blocking）**:

```python
# GOOD: 2つ同時に実行
price, orders = await asyncio.gather(
    client.get("/api/price"),
    client.get("/api/orders")
)
# 合計: 100ms（並行実行）
```

---

### 2.2 WebSocket の仕組みと実装パターン

#### HTTP vs WebSocket

**HTTP（REST API）**:
- クライアント → サーバー: リクエスト
- サーバー → クライアント: レスポンス
- **1往復で接続終了**

**WebSocket**:
- 初回接続後、**持続的なコネクション**を維持
- サーバーからクライアントへ**プッシュ通知**が可能
- リアルタイムデータ（価格、注文状態）に最適

#### WebSocket の接続フロー

```
1. クライアント → サーバー: HTTP Upgrade リクエスト
2. サーバー → クライアント: 101 Switching Protocols
3. WebSocket 接続確立
4. 双方向通信開始
   - Subscribe: {"channel": "price", "symbol": "ETH_USDC"}
   - Server pushes: {"price": 3000.50, "timestamp": ...}
```

#### 実装パターン

```python
import websockets
import json

async def websocket_client():
    uri = "wss://perps.standx.com/ws-stream/v1"

    async with websockets.connect(uri) as ws:
        # Subscribe to price channel
        await ws.send(json.dumps({
            "method": "SUBSCRIBE",
            "params": ["price@ETH_USDC"]
        }))

        # Listen for messages
        async for message in ws:
            data = json.loads(message)
            print(f"Price: {data['mark_price']}")
```

#### 再接続ロジック

WebSocket は切断されることがあります（ネットワーク問題、サーバーメンテナンス等）。**自動再接続**が必須です：

```python
async def websocket_with_reconnect():
    while True:
        try:
            await websocket_client()
        except Exception as e:
            logger.warning(f"WebSocket disconnected: {e}")
            await asyncio.sleep(5)  # 5秒後に再接続
```

---

### 2.3 JWT認証フロー

#### JWT（JSON Web Token）とは

JWT は、**クライアントの身元を証明するトークン**です。

**構造**:

```
<Header>.<Payload>.<Signature>
```

**例**:

```
eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.
eyJzdWIiOiIweDEyMzQiLCJleHAiOjE3MDAwMDAwMDB9.
SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c
```

#### StandX の認証フロー

1. **JWT トークン生成**（ローカル）

```python
import jwt

payload = {
    "wallet_address": "0x1234...",
    "chain": "bsc",
    "exp": int(time.time()) + 604800  # 7日間有効
}

token = jwt.encode(payload, PRIVATE_KEY, algorithm="HS256")
```

2. **リクエストヘッダーに含める**

```python
headers = {
    "Authorization": f"Bearer {token}"
}

response = await client.get("/api/query_open_orders", headers=headers)
```

#### Ed25519 署名（リクエスト署名）

StandX API は、JWT に加えて**リクエスト署名**も要求します：

```python
import hashlib
import base64
from nacl.signing import SigningKey

def sign_request(private_key: str, payload: dict) -> str:
    # ペイロードをJSON文字列化
    message = json.dumps(payload, separators=(',', ':'))

    # Ed25519秘密鍵で署名
    signing_key = SigningKey(bytes.fromhex(private_key))
    signed = signing_key.sign(message.encode())

    # Base64エンコード
    return base64.b64encode(signed.signature).decode()

# ヘッダーに追加
headers = {
    "Authorization": f"Bearer {jwt_token}",
    "X-Signature": sign_request(private_key, payload),
    "X-Session-Id": session_id,
    "X-Sign-Version": "1"
}
```

---

### 2.4 pydantic-settings による設定管理

#### なぜ pydantic-settings を使うのか

**問題**: 環境変数を直接読むとバリデーションが面倒

```python
# BAD: 型安全性なし、デフォルト値管理が煩雑
PRIVATE_KEY = os.environ.get("STANDX_PRIVATE_KEY", "")
ORDER_SIZE = float(os.environ.get("ORDER_SIZE", "0.1"))
```

**解決**: pydantic-settings で型安全＋バリデーション＋ドキュメント化

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    standx_private_key: str
    standx_wallet_address: str
    standx_chain: str = "bsc"

    symbol: str = "ETH_USDC"
    order_size: float = 0.1
    target_distance_bps: float = 8.0
    escape_threshold_bps: float = 3.0

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

# 使用
config = Settings()
print(config.standx_private_key)  # 型安全
```

**メリット**:
- ✅ 型チェック（mypy で検証可能）
- ✅ バリデーション（不正な値を自動検出）
- ✅ デフォルト値の明示
- ✅ ドキュメントとして機能

---

## Part 3: 設計思想とアーキテクチャ

### 3.1 全体アーキテクチャ

#### システム構成

```
┌─────────────────────────────────────────┐
│          StandX MM Bot                  │
├─────────────────────────────────────────┤
│  __main__.py (エントリーポイント)        │
│    ↓                                    │
│  strategy/maker.py (メイン戦略)         │
│    ├→ client/http.py (REST API)        │
│    ├→ client/websocket.py (WebSocket)  │
│    ├→ core/order.py (注文管理)         │
│    ├→ core/escape.py (約定回避)        │
│    ├→ core/risk.py (厳格モード)        │
│    └→ core/distance.py (bps計算)       │
└─────────────────────────────────────────┘
           ↓ REST / WebSocket
┌─────────────────────────────────────────┐
│       StandX API                        │
│  - REST: 注文発注・キャンセル            │
│  - WebSocket: 価格・注文状態・約定通知   │
└─────────────────────────────────────────┘
```

#### レイヤー構造

**Layer 1: Entry Point** (`__main__.py`)
- シグナルハンドリング（Ctrl+C 対応）
- アプリケーション起動

**Layer 2: Strategy** (`strategy/maker.py`)
- 状態管理 (mark_price, bid/ask注文)
- 判断ロジックの統合 (evaluate_order → HOLD/ESCAPE/REPOSITION)
- WebSocket コールバック統合 (価格更新・注文更新・約定検知)

**Layer 3: Core Logic** (`core/`)
- `distance.py`: bps計算 (純粋関数、依存なし)
- `escape.py`: 約定回避 (escape先の価格計算)
- `order.py`: 注文管理 (OrderManager: 発注・キャンセル・再配置)
- `risk.py`: 厳格モード (RiskManager: 約定検知→即クローズ→停止)

**Layer 4: Client** (`client/`)
- `http.py`: REST API 通信 (JWT認証、注文操作)
- `websocket.py`: WebSocket 通信 (価格・注文・約定のリアルタイム受信)
- `exceptions.py`: カスタム例外 (APIError, AuthenticationError, NetworkError)

**Layer 5: Foundation** (`config.py`, `models.py`, `auth.py`)
- 設定管理 (pydantic-settings)
- データモデル (Order, Side, Action, OrderStatus)
- 認証 (EVM/Solana 署名、リクエスト署名)

---

### 3.2 モジュール設計

#### 責務の分離（Separation of Concerns）

**原則**: 各モジュールは**1つの責務**のみを持つ

| モジュール | 責務 | 依存 |
|-----------|------|------|
| `distance.py` | bps計算、閾値判定 | なし |
| `order.py` | 注文管理、再配置 | `distance.py` |
| `escape.py` | 約定回避（約定前） | `distance.py`, `order.py` |
| `risk.py` | 厳格モード（約定後） | `order.py` |
| `maker.py` | 全体統合、状態管理 | すべて |

**なぜ分離するのか**:
- ✅ テストしやすい
- ✅ 変更の影響範囲が限定的
- ✅ 再利用しやすい

#### 例: distance.py

```python
def calculate_distance_bps(order_price: float, mark_price: float) -> float:
    """mark_price から注文価格までの距離を bps で計算"""
    return abs(order_price - mark_price) / mark_price * 10000

def is_within_threshold(distance_bps: float, threshold: float) -> bool:
    """距離が閾値以内かを判定"""
    return distance_bps <= threshold
```

**特徴**:
- 純粋関数（副作用なし）
- 他のモジュールに依存しない
- テストが容易

---

### 3.3 約定回避ロジックの設計

#### 状態遷移図

```
[HOLD] --価格接近--> [ESCAPE] --価格離れる--> [REPOSITION] --> [HOLD]
  ↓                     ↓                         ↓
10bps超えそう         外側に逃げる            目標位置に戻る
  ↓                  (15bps)                  (8bps)
[REPOSITION]
```

#### 判断ロジック

```python
class Action(Enum):
    HOLD = "hold"
    ESCAPE = "escape"
    REPOSITION = "reposition"

def evaluate_order(order: Order, mark_price: float, side: str) -> Action:
    distance = calculate_distance_bps(order.price, mark_price)

    # 1. 価格が接近 → ESCAPE
    if is_approaching(mark_price, order.price, side):
        if distance < ESCAPE_THRESHOLD_BPS:  # 3bps
            return Action.ESCAPE

    # 2. 10bps 境界に近い → REPOSITION
    if distance > 10 - REPOSITION_THRESHOLD_BPS:  # 8bps
        return Action.REPOSITION

    # 3. 目標価格から離れた → REPOSITION
    target_price = calculate_target_price(mark_price, side, TARGET_DISTANCE_BPS)
    if abs(order.price - target_price) / mark_price * 10000 > PRICE_MOVE_THRESHOLD_BPS:
        return Action.REPOSITION

    # 4. 問題なし → HOLD
    return Action.HOLD
```

#### パラメータ設計の根拠

| パラメータ | 値 | 根拠 |
|-----------|-----|------|
| `TARGET_DISTANCE_BPS` | 8 | 10bps境界から2bps内側（安全マージン） |
| `ESCAPE_THRESHOLD_BPS` | 3 | 約定前に逃げられる最小距離 |
| `OUTER_ESCAPE_DISTANCE_BPS` | 15 | 10bps超えだが、価格が戻れば再配置可能 |
| `REPOSITION_THRESHOLD_BPS` | 2 | 10bps境界への接近検知 |
| `PRICE_MOVE_THRESHOLD_BPS` | 5 | 目標価格とのズレ許容範囲 |

---

## Part 4: 数学的モデル

### 4.1 bps 計算の詳細

#### bps（Basis Points）とは

**1 bps = 0.01% = 0.0001**

金融業界で一般的に使われる単位で、パーセント（%）より細かい変動を表現できます。

#### 計算式

```
distance_bps = |order_price - mark_price| / mark_price × 10000
```

**例**:

```python
mark_price = 3000.0
order_price = 2976.0

distance_bps = abs(2976.0 - 3000.0) / 3000.0 * 10000
             = 24.0 / 3000.0 * 10000
             = 0.008 * 10000
             = 80 bps
```

#### 逆算: bps から価格を求める

```python
def calculate_target_price(mark_price: float, side: str, distance_bps: float) -> float:
    """mark_price から指定bps離れた価格を計算"""
    offset = mark_price * (distance_bps / 10000)

    if side == "BUY":  # Bid
        return mark_price - offset
    else:  # SELL / Ask
        return mark_price + offset

# 例
mark_price = 3000.0
target_bid = calculate_target_price(3000.0, "BUY", 8.0)
# = 3000.0 - (3000.0 * 0.0008) = 3000.0 - 2.4 = 2997.6
```

---

### 4.2 距離維持ロジックの数値設計

#### 目標: 10bps 以内を維持しつつ約定を回避

**制約条件**:

1. 報酬条件: mark_price ± 10bps 以内
2. 約定回避: 価格が接近したら逃げる
3. 空白時間: 最小化

**設計パラメータ**:

```
├─ 0bps ─────────────── mark_price
├─ 8bps ─────────────── TARGET_DISTANCE (目標位置)
├─ 10bps ────────────── 報酬境界
├─ 15bps ────────────── OUTER_ESCAPE_DISTANCE (一時退避)
```

#### シミュレーション例

**シナリオ1: 価格が上昇（Bid注文に接近）**

```
1. 初期状態
   mark_price: 3000.0
   bid_order: 2997.6 (8bps)
   距離: 8bps ✅

2. 価格上昇
   mark_price: 3001.0
   bid_order: 2997.6 (変化なし)
   距離: (3001.0 - 2997.6) / 3001.0 * 10000 = 11.3 bps ❌

3. REPOSITION トリガー
   → 新しいbid: 3001.0 - (3001.0 * 0.0008) = 2998.6
   距離: 8bps ✅
```

**シナリオ2: 価格が急降下（Bid注文に接近）**

```
1. 初期状態
   mark_price: 3000.0
   bid_order: 2997.6 (8bps)

2. 価格急降下
   mark_price: 2998.0
   bid_order: 2997.6 (変化なし)
   距離: (2998.0 - 2997.6) / 2998.0 * 10000 = 1.3 bps ⚠️

3. ESCAPE トリガー (< 3bps)
   → キャンセル or 外側に移動 (15bps)
   新しいbid: 2998.0 - (2998.0 * 0.0015) = 2993.5
   距離: 15bps ✅（10bps超えだが一時退避）

4. 価格が戻る
   mark_price: 3000.0
   bid_order: 2993.5 (変化なし)
   距離: (3000.0 - 2993.5) / 3000.0 * 10000 = 21.7 bps

5. REPOSITION トリガー (> PRICE_MOVE_THRESHOLD)
   → 目標位置に戻る (8bps)
   新しいbid: 2997.6
```

---

### 4.3 パラメータチューニング

#### トレードオフの理解

**TARGET_DISTANCE_BPS を小さくする（例: 5bps）**
- ✅ 報酬倍率が高い（100%ゾーン）
- ❌ 約定リスク増加
- ❌ ESCAPE頻度増加（空白時間増加）

**TARGET_DISTANCE_BPS を大きくする（例: 9bps）**
- ✅ 約定リスク減少
- ❌ 報酬境界に近すぎる（再配置頻度増加）
- ❌ 価格変動で10bps超えやすい

**推奨値: 8bps**
- バランスが良い
- 2bps の安全マージン

#### 市場状況に応じた調整

**ボラティリティが高い場合**:

```python
# 保守的な設定
TARGET_DISTANCE_BPS = 7.0  # より内側
ESCAPE_THRESHOLD_BPS = 4.0  # 早めに逃げる
```

**ボラティリティが低い場合**:

```python
# 積極的な設定
TARGET_DISTANCE_BPS = 9.0  # 境界ギリギリ
ESCAPE_THRESHOLD_BPS = 2.0  # ギリギリまで粘る
```

---

## Part 5: 実装ガイド

### 5.1 段階的な実装手順

#### Phase 1: 基盤（config, models, auth）

**目標**: 設定管理、データモデル、認証の基礎を作る

```python
# 1. config.py
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    standx_private_key: str
    standx_wallet_address: str
    # ... 他の設定

# 2. models.py
from pydantic import BaseModel

class Order(BaseModel):
    order_id: str
    symbol: str
    side: str
    price: float
    size: float
    status: str

# 3. auth.py
import jwt

def generate_jwt(private_key: str, wallet_address: str) -> str:
    payload = {...}
    return jwt.encode(payload, private_key, algorithm="HS256")
```

**検証**:

```bash
python -c "from standx_mm_bot.config import Settings; print(Settings())"
```

---

#### Phase 2: API クライアント（http, websocket）

**目標**: StandX API との通信を確立

```python
# client/http.py
import aiohttp

class StandXHTTPClient:
    def __init__(self, base_url: str, jwt_token: str):
        self.base_url = base_url
        self.jwt_token = jwt_token

    async def get_open_orders(self) -> list:
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bearer {self.jwt_token}"}
            async with session.get(f"{self.base_url}/api/query_open_orders", headers=headers) as resp:
                return await resp.json()

# client/websocket.py
import websockets

async def websocket_client(uri: str):
    async with websockets.connect(uri) as ws:
        await ws.send(...)
        async for message in ws:
            print(message)
```

**検証**:

```bash
python -m standx_mm_bot.client.http  # テスト実行
```

---

#### Phase 3: コアロジック（distance, order, escape, risk）

**目標**: 判断ロジックを実装

```python
# core/distance.py
def calculate_distance_bps(order_price: float, mark_price: float) -> float:
    return abs(order_price - mark_price) / mark_price * 10000

# core/order.py
class OrderManager:
    async def place_order(self, side: str, price: float, size: float) -> Order:
        ...

    async def cancel_order(self, order_id: str) -> bool:
        ...

# core/escape.py
def should_escape(distance_bps: float, threshold: float) -> bool:
    return distance_bps < threshold
```

**テスト**:

```python
# tests/test_distance.py
def test_calculate_distance_bps():
    assert calculate_distance_bps(2997.6, 3000.0) == 8.0
```

---

#### Phase 4: 戦略統合（maker, __main__）

**目標**: 全コンポーネントを統合

```python
# strategy/maker.py
class MakerStrategy:
    def __init__(self, config: Settings):
        self.http_client = StandXHTTPClient(...)
        self.order_manager = OrderManager(...)

    async def run(self):
        # WebSocket 監視
        # 判断ロジック実行
        # 注文管理
        ...

# __main__.py
import asyncio
from standx_mm_bot.strategy.maker import MakerStrategy

async def main():
    strategy = MakerStrategy(config)
    await strategy.run()

if __name__ == "__main__":
    asyncio.run(main())
```

**起動**:

```bash
python -m standx_mm_bot
```

---

#### Phase 5: テスト

**目標**: 信頼性の高いコードを確保

```bash
# ユニットテスト
pytest tests/

# 型チェック
mypy .

# Lint
ruff check .

# 全チェック
make check
```

---

### 5.2 よくある問題とデバッグ方法

#### 問題1: WebSocket が切断される

**症状**:

```
WebSocket connection closed
```

**原因**:
- ネットワーク不安定
- サーバー側のタイムアウト

**解決策**:

```python
async def websocket_with_reconnect():
    while True:
        try:
            await websocket_client()
        except Exception as e:
            logger.warning(f"WebSocket disconnected: {e}")
            await asyncio.sleep(5)  # 5秒後に再接続
```

---

#### 問題2: JWT 認証エラー

**症状**:

```
401 Unauthorized
```

**原因**:
- JWT トークンの有効期限切れ
- 秘密鍵が間違っている

**解決策**:

```python
# JWT の有効期限をログで確認
import jwt

decoded = jwt.decode(token, options={"verify_signature": False})
print(f"Expiration: {decoded['exp']}")

# 秘密鍵を確認
print(f"Private key: {PRIVATE_KEY[:10]}...")  # 最初の10文字のみ表示
```

---

#### 問題3: 注文が通らない

**症状**:

```
Order rejected
```

**原因**:
- 価格が mark_price から離れすぎている
- 注文サイズが最小単位未満

**解決策**:

```python
# 価格チェック
distance = calculate_distance_bps(order_price, mark_price)
if distance > 100:
    logger.error(f"Order price too far: {distance} bps")

# サイズチェック
if order_size < MIN_ORDER_SIZE:
    logger.error(f"Order size too small: {order_size}")
```

---

#### 問題4: 約定してしまう

**症状**:

```
Warning: Order filled unexpectedly
```

**原因**:
- 価格が急変して逃げが間に合わなかった
- ESCAPE_THRESHOLD_BPS が小さすぎる

**解決策**:

```python
# ESCAPE_THRESHOLD_BPS を大きくする
ESCAPE_THRESHOLD_BPS = 4.0  # 3.0 → 4.0

# または、厳格モードで即クローズ
if order.status == "FILLED":
    await close_position_immediately()
```

---

## 参考文献・リンク

### プロジェクト内ドキュメント

- [README.md](./README.md) - プロジェクト概要、クイックスタート
- [CONTRIBUTING.md](./CONTRIBUTING.md) - 開発規約、ワークフロー
- [CLAUDE.md](./CLAUDE.md) - AI 向けクイックリファレンス

### StandX 関連

- [StandX API Docs](https://docs.standx.com/standx-api/standx-api)
- [Maker Uptime Program](https://docs.standx.com/docs/stand-x-campaigns/market-maker-uptime-program)
- [Mainnet Campaigns](https://docs.standx.com/docs/stand-x-campaigns/mainnet-campaigns)

### 技術ドキュメント

- [Python asyncio](https://docs.python.org/3/library/asyncio.html)
- [WebSocket Protocol (RFC 6455)](https://datatracker.ietf.org/doc/html/rfc6455)
- [JWT (JSON Web Tokens)](https://jwt.io/)
- [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
- [aiohttp](https://docs.aiohttp.org/)
- [websockets](https://websockets.readthedocs.io/)

### マーケットメイキング理論

- [Investopedia: Market Maker](https://www.investopedia.com/terms/m/marketmaker.asp)
- [Basis Points (bps) Explained](https://www.investopedia.com/terms/b/basispoint.asp)

---

**Last Updated**: 2026-04-10
