# Phase 3-2: 注文管理の実装ガイド

このガイドでは、Phase 3-2で実装した注文管理（`core/order.py`）について詳しく解説します。

**対象読者**: Python初心者、非同期プログラミング初心者、Market Making初心者

**関連Issue**: [#17 Phase 3-2: 注文管理の実装](https://github.com/zomians/standx_mm_bot/issues/17)

**実装PR**: [#63 Phase 3-2: 注文管理の実装](https://github.com/zomians/standx_mm_bot/pull/63)

---

## 目次

1. [概要](#概要)
2. [ALO (Add Liquidity Only) の基礎](#alo-add-liquidity-only-の基礎)
3. [OrderManager.place_order()の実装解説](#ordermanagerplace_orderの実装解説)
4. [OrderManager.cancel_order()の実装解説](#ordermanagercancel_orderの実装解説)
5. [OrderManager.reposition_order()の実装解説](#ordermanagerreposition_orderの実装解説)
6. [asyncio.Lockの必要性](#asynciolockの必要性)
7. [テストケース設計のベストプラクティス](#テストケース設計のベストプラクティス)
8. [設計判断の記録](#設計判断の記録)
9. [まとめ](#まとめ)

---

## 概要

### Phase 3-2の目的

Market Making Botにおいて、**約定させずに板に居続ける**ためには、以下の機能が必要です：

1. **注文発注**: ALOを使ってMaker注文のみを発注
2. **注文キャンセル**: 価格が接近したら安全にキャンセル
3. **注文再配置**: 空白時間を最小化しながら注文を動かす

Phase 3-2では、`OrderManager`クラスを実装し、これらの機能を提供しました。

### 実装したモジュール

| モジュール | 責務 | メソッド数 |
|-----------|------|-----------|
| `core/order.py` | 注文管理（発注・キャンセル・再配置） | 6 |

### なぜ重要なのか

```
空白時間 = Maker Uptime減少
```

StandXのMaker Uptimeは「両サイド±10bps以内に、毎時30分以上いること」が条件です。注文を再配置する際に空白時間が発生すると、Uptimeにカウントされない時間が生まれます。

そのため、**空白時間を最小化する注文管理**が重要です。

---

## ALO (Add Liquidity Only) の基礎

### ALOとは

**ALO (Add Liquidity Only)** は、StandX APIの`time_in_force`パラメータの1つで、**即座に約定しない注文**を保証します。

```python
time_in_force="alo"  # Add Liquidity Only
```

### StandX APIの`time_in_force`の種類

| 値 | 説明 | 用途 |
|---|---|---|
| `gtc` | Good Til Canceled | 通常の注文 |
| `ioc` | Immediate Or Cancel | 即座に約定、残りはキャンセル |
| `alo` | **Add Liquidity Only** | **Maker注文専用（即座に約定しない）** |

### ALOの特徴

StandX API仕様より：

> **"book without immediate execution"**
> - 注文が板に追加されるが、即座には約定しない
>
> **"only executes as resting order"**
> - 板に乗った状態（resting order）でのみ約定
> - Taker注文として約定することはない

### なぜALOを使うのか

#### Botの設計思想との完璧な適合

```
約定 = 失敗
```

Botの目的は「約定させずに板に居続けること」です。ALOを使うことで：

- ✅ **即座に約定しない**（price slippageで約定するリスクゼロ）
- ✅ **Maker注文のみ**（Taker手数料ゼロ）
- ✅ **設計思想に完璧に適合**

#### 通常の`gtc`との違い

```python
# gtc（通常注文）の場合
time_in_force="gtc"
# → 板の反対側の注文とマッチすれば即座に約定（Taker注文になる可能性）

# alo（ALO）の場合
time_in_force="alo"
# → 板に追加されるのみ、即座には約定しない（常にMaker注文）
```

### 実装例

```python
async def place_order(self, side: Side, price: float, size: float) -> Order:
    """注文を発注（ALO: Add Liquidity Only）"""

    response = await self.client.new_order(
        symbol=self.config.symbol,
        side=side.value.lower(),
        price=price,
        size=size,
        order_type="limit",
        time_in_force="alo",  # ← ここでALOを指定
        reduce_only=False
    )

    return self._parse_order_response(response, side, price, size)
```

---

## OrderManager.place_order()の実装解説

### 責務

注文を発注し、Order型のオブジェクトを返す。

### シグネチャ

```python
async def place_order(
    self,
    side: Side,              # 注文サイド（BUY/SELL）
    price: float,            # 注文価格
    size: float,             # 注文サイズ
    time_in_force: str = "alo"  # 注文有効期限（デフォルト: alo）
) -> Order:
```

### 実装の流れ

```python
async def place_order(
    self,
    side: Side,
    price: float,
    size: float,
    time_in_force: str = "alo",
) -> Order:
    # 1. asyncio.Lockで排他制御
    async with self._lock:
        # 2. ログ出力
        logger.info(
            f"Placing {side.value} order: price={price:.2f}, size={size}, "
            f"time_in_force={time_in_force}"
        )

        # 3. HTTP APIを呼び出し
        response = await self.client.new_order(
            symbol=self.config.symbol,
            side=side.value.lower(),
            price=price,
            size=size,
            order_type="limit",
            time_in_force=time_in_force,  # ALOを指定
            reduce_only=False,
        )

        # 4. レスポンスをパース
        order = self._parse_order_response(response, side, price, size)

        # 5. ログ出力
        logger.info(f"Order placed: order_id={order.id}, status={order.status}")

        return order
```

### ポイント1: asyncio.Lockによる排他制御

```python
async with self._lock:
    # ここでの操作は排他的に実行される
```

**なぜ必要か？**

複数の注文を同時に発注しようとした場合、競合状態（race condition）が発生する可能性があります。`asyncio.Lock`を使うことで、**1つずつ順番に処理**されることを保証します。

詳細は「[asyncio.Lockの必要性](#asynciolockの必要性)」セクションを参照。

### ポイント2: レスポンスのパース

StandX APIのレスポンス形式は複数あります：

```python
# パターン1: order_idが返る
{
    "order_id": "xxx",
    "status": "OPEN"
}

# パターン2: request_idのみ
{
    "code": 0,
    "message": "success",
    "request_id": "xxx"
}
```

`_parse_order_response()`は両方に対応しています：

```python
def _parse_order_response(self, response: dict, side: Side, price: float, size: float) -> Order:
    # order_id または request_id を取得
    order_id = response.get("order_id") or response.get("request_id", "unknown")

    # status を取得（デフォルトはOPEN）
    status_str = response.get("status", "OPEN")
    try:
        status = OrderStatus(status_str)
    except ValueError:
        status = OrderStatus.OPEN

    return Order(
        id=order_id,
        symbol=self.config.symbol,
        side=side,
        price=price,
        size=size,
        order_type=OrderType.LIMIT,
        status=status,
    )
```

---

## OrderManager.cancel_order()の実装解説

### 責務

指定した注文IDの注文をキャンセルする。

### シグネチャ

```python
async def cancel_order(self, order_id: str) -> None:
```

### 実装の流れ

```python
async def cancel_order(self, order_id: str) -> None:
    # 1. asyncio.Lockで排他制御
    async with self._lock:
        # 2. ログ出力
        logger.info(f"Cancelling order: order_id={order_id}")

        # 3. HTTP APIを呼び出し
        await self.client.cancel_order(
            order_id=order_id,
            symbol=self.config.symbol,
        )

        # 4. ログ出力
        logger.info(f"Order cancelled: order_id={order_id}")
```

### ポイント: asyncio.Lockの重要性

キャンセルと発注が同時に実行されると、以下の問題が発生する可能性があります：

```python
# 問題例（Lockがない場合）
asyncio.gather(
    order_mgr.place_order(Side.BUY, 3500.0, 0.001),  # タスク1
    order_mgr.cancel_order("old123")                 # タスク2
)
# → 両方のAPIリクエストが同時に飛ぶ
# → StandX API側で競合が発生する可能性
```

`asyncio.Lock`により、**1つずつ順番に実行**されることが保証されます。

---

## OrderManager.reposition_order()の実装解説

### 責務

既存の注文を新しい価格に再配置する。

### シグネチャ

```python
async def reposition_order(
    self,
    old_order_id: str,      # 旧注文ID
    new_price: float,       # 新しい価格
    side: Side,             # 注文サイド
    size: float,            # 注文サイズ
    strategy: Literal["place_first", "cancel_first"] = "place_first"
) -> Order:
```

### 2つの戦略

#### 戦略1: 発注先行（place_first）【デフォルト】

```python
strategy="place_first"
```

**フロー:**

```
1. 新規注文を発注
2. 新規注文がOPENになったことを確認
3. 旧注文をキャンセル
```

**メリット:**
- ✅ **空白時間ゼロ**（常に板に注文が存在）
- ✅ **Maker Uptime最大化**

**デメリット:**
- ❌ 一時的に両方の注文が存在（証拠金が2倍必要）
- ❌ 資金効率が悪い

#### 戦略2: キャンセル先行（cancel_first）

```python
strategy="cancel_first"
```

**フロー:**

```
1. 旧注文をキャンセル
2. 新規注文を発注
```

**メリット:**
- ✅ **資金効率優先**（片方の証拠金のみ）

**デメリット:**
- ❌ **空白時間が発生**（板から一時的に消える）
- ❌ Maker Uptimeに影響

### タイムライン図解

#### 発注先行（place_first）

```
時刻  板の状態        Maker Uptime  証拠金
t0   [旧注文]        ✅ カウント中   1x
t1   [旧][新]        ✅ カウント中   2x （一時的）
t2   [新注文]        ✅ カウント中   1x
     ↑
     空白時間ゼロ
```

#### キャンセル先行（cancel_first）

```
時刻  板の状態        Maker Uptime  証拠金
t0   [旧注文]        ✅ カウント中   1x
t1   []             ❌ 空白時間     0x
t2   [新注文]        ✅ カウント中   1x
     ↑
     空白時間あり（Uptimeに影響）
```

### 実装の流れ（発注先行）

```python
async def reposition_order(
    self,
    old_order_id: str,
    new_price: float,
    side: Side,
    size: float,
    strategy: Literal["place_first", "cancel_first"] = "place_first",
) -> Order:
    # asyncio.Lockで排他制御
    async with self._lock:
        logger.info(
            f"Repositioning order: old_order_id={old_order_id}, "
            f"new_price={new_price:.2f}, strategy={strategy}"
        )

        if strategy == "place_first":
            # 1. 新規注文を発注
            new_order = await self._place_order_unlocked(side, new_price, size)

            # 2. 新規注文がOPENであることを確認
            if new_order.status == OrderStatus.OPEN:
                # 3. 旧注文をキャンセル
                await self._cancel_order_unlocked(old_order_id)
                logger.info(f"Reposition completed (place_first): new_order_id={new_order.id}")
            else:
                # 新規注文が失敗した場合、旧注文は残す
                logger.warning(
                    f"New order not OPEN (status={new_order.status}), "
                    f"old order not cancelled"
                )

            return new_order

        else:  # cancel_first
            # 1. 旧注文をキャンセル
            await self._cancel_order_unlocked(old_order_id)
            # 2. 新規注文を発注
            new_order = await self._place_order_unlocked(side, new_price, size)

            logger.info(f"Reposition completed (cancel_first): new_order_id={new_order.id}")

            return new_order
```

### ポイント1: _unlocked系メソッドの使用

```python
async def _place_order_unlocked(self, side: Side, price: float, size: float) -> Order:
    """注文を発注（ロックなし、内部使用専用）"""
    # Lockを取らずに注文発注
```

**なぜ必要か？**

`reposition_order()`は既に`async with self._lock:`でロックを取得しています。その中で`place_order()`を呼ぶと、**2重ロック**になってデッドロックします。

そのため、ロックを取らない`_unlocked`系メソッドを用意しています。

### ポイント2: 新規注文失敗時の保護

```python
if new_order.status == OrderStatus.OPEN:
    await self._cancel_order_unlocked(old_order_id)
else:
    logger.warning(f"New order not OPEN (status={new_order.status}), old order not cancelled")
```

新規注文が失敗した場合（例: FILLED、残高不足など）、**旧注文をキャンセルしない**ことで、板から完全に消えることを防ぎます。

### 戦略の選択基準

| 状況 | 推奨戦略 | 理由 |
|------|---------|------|
| **通常時** | `place_first` | Maker Uptime最大化（空白時間ゼロ） |
| **残高不足時** | `cancel_first` | 資金効率優先（両方の証拠金が用意できない） |
| **価格急変時** | `place_first` | 安全性優先（旧注文が残る保証） |

**デフォルトは`place_first`** としています。Botの目的は「Maker Uptimeを最大化すること」だからです。

---

## asyncio.Lockの必要性

### 問題: 並行処理の競合

Pythonの`asyncio`では、複数のタスクが**並行（concurrent）**に実行されます。

```python
# 問題例
order_mgr = OrderManager(client, config)

# 同時に2つの操作を実行
await asyncio.gather(
    order_mgr.place_order(Side.BUY, 3500.0, 0.001),  # タスク1
    order_mgr.cancel_order("old123")                 # タスク2
)
```

このコードは、**2つのタスクが同時に実行**されます。

### 並行処理の危険性

#### ケース1: APIリクエストの競合

```python
タスク1: place_order() を開始
  → HTTP POST /api/new_order を送信中...

タスク2: cancel_order() を開始
  → HTTP POST /api/cancel_order を送信中...

# 両方のリクエストが同時に StandX API に到達
# → API側で競合が発生する可能性
```

#### ケース2: インターリーブの問題

```python
タスク1: place_order()
  ステップ1: order_id取得
  ステップ2: ログ出力
  ↓
  【ここでタスク2が割り込む】
  ↓
タスク2: cancel_order()
  ステップ1: order_id取得
  ステップ2: キャンセルAPI呼び出し
  ステップ3: ログ出力
  ↓
  【タスク1に戻る】
  ↓
タスク1: place_order()
  ステップ3: ログ出力

# ログの順序がおかしくなる
# デバッグが困難
```

### 解決策: asyncio.Lock

```python
class OrderManager:
    def __init__(self, http_client: StandXHTTPClient, config: Settings):
        self.client = http_client
        self.config = config
        self._lock = asyncio.Lock()  # ← Lockを作成

    async def place_order(self, ...):
        async with self._lock:  # ← Lockを取得
            # ここは排他的に実行される
            # 他のタスクは待機
```

### Lockの動作

```python
# 同時に2つの操作を実行
await asyncio.gather(
    order_mgr.place_order(Side.BUY, 3500.0, 0.001),  # タスク1
    order_mgr.cancel_order("old123")                 # タスク2
)

# 実際の実行順序（Lockあり）
タスク1: place_order() を開始
  → Lockを取得
  → HTTP POST /api/new_order
  → Lockを解放

タスク2: cancel_order() を開始（タスク1の完了後）
  → Lockを取得
  → HTTP POST /api/cancel_order
  → Lockを解放

# 順番に実行される（競合なし）
```

### テストでの確認

```python
async def test_concurrent_place_orders():
    """複数の注文を同時に発注しても競合しないことを確認."""
    call_order = []

    async def mock_new_order(*_args, **_kwargs):
        call_order.append("start")
        await asyncio.sleep(0.01)  # 処理に時間がかかる想定
        call_order.append("end")
        return {"order_id": "test", "status": "OPEN"}

    mock_client.new_order.side_effect = mock_new_order

    order_mgr = OrderManager(mock_client, config)

    # 3つの注文を同時に発注
    await asyncio.gather(
        order_mgr.place_order(Side.BUY, 3500.0, 0.001),
        order_mgr.place_order(Side.BUY, 3510.0, 0.001),
        order_mgr.place_order(Side.BUY, 3520.0, 0.001),
    )

    # Lockにより順序が保証される（インターリーブしない）
    assert call_order == [
        "start", "end",  # 1つ目完了
        "start", "end",  # 2つ目完了
        "start", "end",  # 3つ目完了
    ]
```

**Lockがない場合:**

```python
call_order == [
    "start", "start", "start",  # 全部開始
    "end", "end", "end",        # 全部終了
]
# インターリーブ発生
```

---

## テストケース設計のベストプラクティス

Phase 3-2では、**モックテスト（7件）** と **統合テスト（2件）** を実装しました。

### モックテストの設計

#### 1. 基本的な発注テスト

```python
async def test_place_order_success():
    """注文発注が成功することを確認."""
    mock_client.new_order.return_value = {
        "order_id": "test123",
        "status": "OPEN",
    }

    order_mgr = OrderManager(mock_client, config)
    order = await order_mgr.place_order(Side.BUY, 3500.0, 0.001)

    # 注文情報の確認
    assert order.id == "test123"
    assert order.status == OrderStatus.OPEN

    # API呼び出しの確認（ALOが指定されていることを確認）
    mock_client.new_order.assert_called_once_with(
        symbol="ETH-USD",
        side="buy",
        price=3500.0,
        size=0.001,
        order_type="limit",
        time_in_force="alo",  # ← ALO確認
        reduce_only=False,
    )
```

#### 2. レスポンス形式の柔軟性テスト

```python
async def test_place_order_with_request_id():
    """request_idのみのレスポンスでも動作することを確認."""
    # レスポンスにorder_idがない場合
    mock_client.new_order.return_value = {
        "code": 0,
        "message": "success",
        "request_id": "req456",
    }

    order_mgr = OrderManager(mock_client, config)
    order = await order_mgr.place_order(Side.SELL, 3600.0, 0.001)

    # request_idがorder_idとして使われる
    assert order.id == "req456"
    assert order.status == OrderStatus.OPEN  # デフォルト
```

#### 3. 再配置の順序検証テスト

```python
async def test_reposition_place_first():
    """発注先行の再配置が正しく動作することを確認."""
    mock_client.new_order.return_value = {"order_id": "new123", "status": "OPEN"}
    mock_client.cancel_order.return_value = {"status": "CANCELLED"}

    order_mgr = OrderManager(mock_client, config)
    await order_mgr.reposition_order(
        old_order_id="old123",
        new_price=3550.0,
        side=Side.BUY,
        size=0.001,
        strategy="place_first",
    )

    # 呼び出し順序の確認（発注が先）
    call_order = list(mock_client.method_calls)
    new_order_call = next(i for i, call in enumerate(call_order) if call[0] == "new_order")
    cancel_call = next(i for i, call in enumerate(call_order) if call[0] == "cancel_order")
    assert new_order_call < cancel_call  # ← 順序確認
```

#### 4. 新規注文失敗時の保護テスト

```python
async def test_reposition_new_order_not_open():
    """新規注文がOPENでない場合、旧注文がキャンセルされないことを確認."""
    # 新規注文が失敗（FILLED）
    mock_client.new_order.return_value = {"order_id": "new789", "status": "FILLED"}

    order_mgr = OrderManager(mock_client, config)
    new_order = await order_mgr.reposition_order(
        old_order_id="old789",
        new_price=3500.0,
        side=Side.BUY,
        size=0.001,
        strategy="place_first",
    )

    # 新規注文は返される
    assert new_order.id == "new789"
    assert new_order.status == OrderStatus.FILLED

    # 旧注文はキャンセルされない
    assert mock_client.cancel_order.call_count == 0  # ← 保護確認
```

#### 5. 並行処理の競合防止テスト

```python
async def test_concurrent_place_orders():
    """複数の注文を同時に発注しても競合しないことを確認."""
    call_order = []

    async def mock_new_order(*_args, **_kwargs):
        call_order.append("start")
        await asyncio.sleep(0.01)
        call_order.append("end")
        return {"order_id": f"order{len(call_order)}", "status": "OPEN"}

    mock_client.new_order.side_effect = mock_new_order
    order_mgr = OrderManager(mock_client, config)

    # 3つの注文を同時に発注
    await asyncio.gather(
        order_mgr.place_order(Side.BUY, 3500.0, 0.001),
        order_mgr.place_order(Side.BUY, 3510.0, 0.001),
        order_mgr.place_order(Side.BUY, 3520.0, 0.001),
    )

    # Lockにより順序が保証される（インターリーブしない）
    assert call_order == ["start", "end", "start", "end", "start", "end"]
```

### 統合テストの設計

#### 1. 実注文の発注→キャンセル

```python
@pytest.mark.integration
async def test_place_and_cancel_order_real(real_config: Settings):
    """
    実注文テスト: 注文発注 → キャンセル.

    前提条件:
    - StandXに$10以上入金済み
    - ORDER_SIZE=0.001

    手順:
    1. 現在価格を取得
    2. 約定しない価格で注文発注（30bps離す）
    3. 即座にキャンセル（1秒以内）
    4. Position=0を確認
    """
    async with StandXHTTPClient(real_config) as client:
        # 現在価格を取得
        price_data = await client.get_symbol_price(real_config.symbol)
        mark_price = float(price_data["mark_price"])

        # 約定しない価格（BUY: 3%下）
        far_buy_price = round(mark_price * 0.97, 2)

        order_mgr = OrderManager(client, real_config)

        # 注文発注
        order = await order_mgr.place_order(Side.BUY, far_buy_price, 0.001)
        assert order.status == OrderStatus.OPEN

        # 即座にキャンセル
        await asyncio.sleep(0.5)
        await order_mgr.cancel_order(order.id)

        # Position確認
        position = await client.get_position(real_config.symbol)
        # Position=0を確認（約定していないことを確認）
```

#### 2. 実注文の再配置

```python
@pytest.mark.integration
async def test_reposition_order_real(real_config: Settings):
    """
    実注文テスト: 注文再配置.

    手順:
    1. 遠い価格で初回注文
    2. 別の遠い価格に再配置
    3. 両方キャンセル
    4. Position=0を確認
    """
    async with StandXHTTPClient(real_config) as client:
        # 現在価格を取得
        price_data = await client.get_symbol_price(real_config.symbol)
        mark_price = float(price_data["mark_price"])

        # 約定しない価格
        far_price_1 = round(mark_price * 0.97, 2)
        far_price_2 = round(mark_price * 0.96, 2)

        order_mgr = OrderManager(client, real_config)

        # 初回注文
        order1 = await order_mgr.place_order(Side.BUY, far_price_1, 0.001)
        await asyncio.sleep(0.5)

        # 再配置（発注先行）
        order2 = await order_mgr.reposition_order(
            old_order_id=order1.id,
            new_price=far_price_2,
            side=Side.BUY,
            size=0.001,
            strategy="place_first",
        )

        assert order2.id != order1.id  # 新規注文が作成されている

        # クリーンアップ
        await order_mgr.cancel_order(order2.id)

        # Position確認
        position = await client.get_position(real_config.symbol)
        # Position=0を確認
```

### なぜ統合テストは手動実行なのか

```python
@pytest.mark.integration  # ← このマークで除外
```

**理由:**

1. **実APIを使用** → ネットワーク、API状態に依存
2. **入金が必要** → テスト実行前の準備が必要
3. **約定リスク** → 価格急変時に約定する可能性
4. **CI/CDに不向き** → 環境構築が困難

そのため、`make test`では除外し、**手動実行のみ**としています。

```bash
# 統合テストの実行（手動）
pytest tests/test_order.py::test_place_and_cancel_order_real -v -s
```

---

## 設計判断の記録

### 判断1: なぜ発注先行をデフォルトにしたのか

**理由:** Botの目的は「Maker Uptimeを最大化すること」だから。

```python
strategy: Literal["place_first", "cancel_first"] = "place_first"  # ← デフォルト
```

**背景:**

StandXのMaker Uptimeは「両サイド±10bps以内に、毎時30分以上いること」が条件。空白時間が発生すると、Uptimeにカウントされない時間が生まれる。

**トレードオフ:**

| 項目 | 発注先行 | キャンセル先行 |
|------|---------|--------------|
| 空白時間 | ゼロ | あり |
| 資金効率 | 悪い（2x） | 良い（1x） |
| Maker Uptime | 最大化 | 減少 |

**結論:** Botの目的から、**空白時間ゼロ（発注先行）** を優先。

### 判断2: なぜasyncio.Lockが必要なのか

**理由:** 並行処理の競合を防ぐため。

**背景:**

`asyncio`では複数のタスクが並行実行される。注文発注とキャンセルが同時に実行されると、APIリクエストが競合する可能性がある。

**実装:**

```python
class OrderManager:
    def __init__(self, ...):
        self._lock = asyncio.Lock()

    async def place_order(self, ...):
        async with self._lock:  # ← 排他制御
            # ...
```

**結論:** 安全性のため、**全てのAPI操作をLockで保護**。

### 判断3: なぜ_unlocked系メソッドを作ったのか

**理由:** `reposition_order()`内で2重ロックを防ぐため。

**背景:**

`reposition_order()`は既にLockを取得している。その中で`place_order()`を呼ぶと、2重ロックになりデッドロック。

**実装:**

```python
async def reposition_order(self, ...):
    async with self._lock:  # ← 既にロック取得
        # ロックを取らないメソッドを使用
        new_order = await self._place_order_unlocked(...)
        await self._cancel_order_unlocked(...)

async def _place_order_unlocked(self, ...):
    """ロックなし版（内部使用専用）"""
    # Lockを取らない
```

**結論:** デッドロック防止のため、**内部専用の_unlocked系メソッドを実装**。

### 判断4: 統合テストを手動実行にした理由

**理由:** CI/CDで自動実行するとリスクが高いため。

**背景:**

- 実APIを使用 → 約定リスクあり
- 入金が必要 → 環境構築が困難
- ネットワーク依存 → テストが不安定

**実装:**

```python
@pytest.mark.integration  # ← CI/CDで除外
async def test_place_and_cancel_order_real(...):
    # 実注文テスト
```

**結論:** 安全性のため、**統合テストは手動実行のみ**。

---

## まとめ

### Phase 3-2で実装した内容

| 項目 | 内容 |
|------|------|
| **モジュール** | `core/order.py` |
| **クラス** | `OrderManager` |
| **メソッド** | `place_order()`, `cancel_order()`, `reposition_order()` |
| **重要概念** | ALO, asyncio.Lock, 発注先行/キャンセル先行 |
| **テスト** | モックテスト7件、統合テスト2件 |

### 重要なポイント

1. **ALO (Add Liquidity Only)**: 即座に約定しない注文を保証
2. **発注先行（デフォルト）**: 空白時間ゼロでMaker Uptime最大化
3. **asyncio.Lock**: 並行処理の競合を防止
4. **新規注文失敗時の保護**: 旧注文を残すことで板から完全に消えることを防ぐ

### 次のステップ

**Phase 3-3**: 厳格モードの実装（`core/risk.py`）

- 約定検知
- 即座に成行でポジションクローズ
- ポジションゼロ確認
- エラーログ出力 → Bot終了

**Phase 4**: 戦略統合（`strategy/maker.py` + `__main__.py`）

- WebSocketと注文管理を統合
- 価格更新時の判断ロジック
- アクション実行（ESCAPE, REPOSITION, HOLD）

---

**実装PR**: [#63 Phase 3-2: 注文管理の実装](https://github.com/zomians/standx_mm_bot/pull/63)

**次のチュートリアル**: [Phase 3-3: 厳格モードの実装ガイド](./phase3-3-risk-management.md)
