# Phase 4: 戦略統合の実装ガイド

このガイドでは、Phase 4で実装した戦略統合（`strategy/maker.py` + `__main__.py`）について詳しく解説します。

**対象読者**: Python初心者、非同期プログラミング初心者、Market Making初心者

**関連Issue**: [#19 Phase 4: 戦略統合の実装](https://github.com/zomians/standx_mm_bot/issues/19)

**実装PR**: [#71 Phase 4: 戦略統合の実装](https://github.com/zomians/standx_mm_bot/pull/71)

---

## 目次

1. [概要](#概要)
2. [なぜ戦略統合が必要なのか](#なぜ戦略統合が必要なのか)
3. [MakerStrategyの全体像](#makerstrategyの全体像)
4. [evaluate_order()の実装解説](#evaluate_orderの実装解説)
5. [WebSocketコールバックの実装解説](#websocketコールバックの実装解説)
6. [アクション実行の実装解説](#アクション実行の実装解説)
7. [ライフサイクル管理](#ライフサイクル管理)
8. [エントリーポイント(__main__.py)の実装解説](#エントリーポイント__mainpyの実装解説)
9. [in_flightフラグによる重複防止](#in_flightフラグによる重複防止)
10. [テストケース設計](#テストケース設計)
11. [設計判断の記録](#設計判断の記録)
12. [まとめ](#まとめ)

---

## 概要

### Phase 4の目的

Phase 1〜3で個別に実装してきたコンポーネントをすべて統合し、**Bot を実際に動作可能にする**のが Phase 4 の目的です。

```
Phase 1: 基盤 (config, auth, HTTP client, WebSocket client)
Phase 2: 通信 (WebSocket認証・チャンネル購読)
Phase 3: コアロジック (escape, order, risk)
Phase 4: 統合 (strategy/maker.py + __main__.py)  ← 今回
```

### 実装したモジュール

| モジュール | 責務 | クラス/関数 |
|-----------|------|-----------|
| `strategy/maker.py` | 全コンポーネントの統合・判断ロジック | `MakerStrategy` |
| `__main__.py` | エントリーポイント・シグナルハンドリング | `main()` |

### 変更したモジュール

| モジュール | 変更内容 |
|-----------|---------|
| `client/websocket.py` | JWT認証の追加（`_authenticate()`, order/tradeチャンネル購読） |
| `models.py` | `str, Enum` → `StrEnum` への移行（Ruff UP042対応） |

---

## なぜ戦略統合が必要なのか

### Phase 3までの状態

Phase 3までに以下のコンポーネントが**個別に**実装されていました：

```
client/http.py     → REST API呼び出し
client/websocket.py → WebSocket接続・メッセージ受信
core/distance.py   → 距離計算
core/escape.py     → 約定回避価格計算
core/order.py      → 注文管理（発注・キャンセル・再配置）
core/risk.py       → リスク管理（即座ポジションクローズ）
```

しかし、これらは**それぞれ独立しており、誰が判断して誰が実行するかが決まっていません**でした。

### 戦略統合の役割

`MakerStrategy` は**オーケストレーター**です。各コンポーネントを指揮して Bot を駆動します。

```
WebSocket (価格更新) ──→ MakerStrategy ──→ evaluate_order() ──→ 判断
                              │
                              ├──→ OrderManager (注文操作)
                              ├──→ RiskManager (約定時クローズ)
                              └──→ distance/escape (計算)
```

### 人間のトレーダーとの対比

```
人間のトレーダー:
  板を見る → 価格が近い？遠い？ → 注文を移動 or 放置

MakerStrategy:
  WebSocketで価格受信 → evaluate_order() → ESCAPE / REPOSITION / HOLD
```

---

## MakerStrategyの全体像

### 状態管理

```python
class MakerStrategy:
    def __init__(self, config: Settings):
        self.config = config
        self.mark_price: float = 0.0          # 最新の mark_price
        self.bid_order: Order | None = None    # BUY 注文
        self.ask_order: Order | None = None    # SELL 注文
        self._shutdown_event = asyncio.Event() # 停止シグナル
        self._bid_in_flight: bool = False      # BUY 処理中フラグ
        self._ask_in_flight: bool = False      # SELL 処理中フラグ
        self._initial_orders_placed: bool = False  # 初期注文済みフラグ
        self._exit_code: int = 0               # 終了コード

        # run() 内で初期化されるコンポーネント
        self.http_client: StandXHTTPClient | None = None
        self.ws_client: StandXWebSocketClient | None = None
        self.order_manager: OrderManager | None = None
        self.risk_manager: RiskManager | None = None
```

### なぜ `run()` 内で初期化するのか

HTTPクライアントは `async with` で管理する必要があります（セッションのライフサイクル）。`__init__` は同期メソッドなので、非同期初期化ができません。

```python
# ❌ __init__で初期化できない
def __init__(self):
    self.http_client = await StandXHTTPClient(config).__aenter__()  # SyntaxError

# ✅ run()内でasync withを使う
async def run(self):
    async with StandXHTTPClient(self.config) as http_client:
        self.http_client = http_client
        # ...
```

---

## evaluate_order()の実装解説

### 責務

注文の現在状態を評価し、実行すべきアクション（ESCAPE / REPOSITION / HOLD）を決定する。

### 判断フロー

```
mark_price と order_price の関係を評価

  1. 価格が近づいている & 距離 < 3bps?
     → YES: ESCAPE（約定回避が最優先）

  2. 距離 > 8bps (= 10 - reposition_threshold)?
     → YES: REPOSITION（10bps境界から離れすぎ）

  3. 目標価格からの乖離 > 5bps?
     → YES: REPOSITION（ドリフト修正）

  4. それ以外
     → HOLD（現状維持）
```

### 実装

```python
def evaluate_order(self, order: Order, mark_price: float, side: Side) -> Action:
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
    target_price = calculate_target_price(
        mark_price, side, self.config.target_distance_bps
    )
    price_diff_bps = abs(order.price - target_price) / mark_price * 10000
    if price_diff_bps > self.config.price_move_threshold_bps:
        return Action.REPOSITION

    return Action.HOLD
```

### ポイント1: 3段階の優先順位

| 優先順位 | 条件 | アクション | 理由 |
|---------|------|-----------|------|
| 1 | 接近中 & 距離 < 3bps | ESCAPE | **約定回避が最優先** |
| 2 | 距離 > 8bps | REPOSITION | 10bps境界に近すぎると報酬条件を満たせない |
| 3 | 目標乖離 > 5bps | REPOSITION | mark_priceの変動に追従 |
| - | それ以外 | HOLD | 不要な操作を避ける |

### ポイント2: `is_approaching` の意味

「接近している」とは、mark_price が注文価格に向かって動いていることです。

```
BUY 注文の場合:
  mark_price < order_price → 接近中（上から近づいている）
  mark_price > order_price → 離れている

SELL 注文の場合:
  mark_price > order_price → 接近中（下から近づいている）
  mark_price < order_price → 離れている
```

なぜこれが重要か：距離が3bps以内でも、**離れている方向に動いている**なら逃げる必要はありません。

### ポイント3: `10 - reposition_threshold_bps` の意味

報酬条件は `mark_price ± 10bps 以内`。`reposition_threshold_bps = 2` の場合：

```
10 - 2 = 8bps

→ 距離が 8bps を超えたら REPOSITION
→ つまり 10bps 境界まで 2bps のバッファ内に入ったら修正
```

### 具体例

```
config:
  target_distance_bps = 8.0
  escape_threshold_bps = 3.0
  reposition_threshold_bps = 2.0
  price_move_threshold_bps = 5.0

mark_price = 2500.0

BUY 注文 (price=2498.0, 距離=8bps):
  → 離れている方向 → ESCAPE ではない
  → 8bps > 8bps? → No
  → 目標(2498.0)との乖離=0bps → HOLD ✅

BUY 注文 (price=2499.5, 距離=2bps):
  → mark < order → 接近中
  → 2bps < 3bps → ESCAPE ✅

BUY 注文 (price=2477.0, 距離=92bps):
  → 92bps > 8bps → REPOSITION ✅
```

---

## WebSocketコールバックの実装解説

MakerStrategy は3つのWebSocketコールバックを登録します。

### コールバック一覧

| コールバック | チャンネル | 発火タイミング |
|------------|----------|--------------|
| `_on_price_update` | price | mark_price更新時 |
| `_on_order_update` | order | 注文ステータス変更時 |
| `_on_trade` | trade | 約定発生時 |

### _on_price_update: 価格更新コールバック

```python
async def _on_price_update(self, data: dict[str, Any]) -> None:
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
```

**ポイント: 初回価格受信で初期注文を発注**

Bot起動時、mark_price が不明な状態では注文を出せません。最初の価格更新を受信してから初期注文を発注します。

```
Bot起動 → WS接続 → price受信 (mark=2500.0) → 初期注文発注
                                                ├─ BUY @ 2498.0 (8bps下)
                                                └─ SELL @ 2502.0 (8bps上)
```

**ポイント: 片側欠落の自動補充**

ESCAPEでキャンセルフォールバックが発生すると、片側の注文が `None` になります。次の価格更新時に自動的に再発注します。

### _on_order_update: 注文更新コールバック

```python
async def _on_order_update(self, data: dict[str, Any]) -> None:
    order_id = data.get("order_id")
    status_str = data.get("status", "")

    target_order, side = self._match_order(order_id)
    if target_order is None:
        return

    if status_str == "CANCELED":
        if side == Side.BUY:
            self.bid_order = None
        else:
            self.ask_order = None

    elif status_str == "FILLED":
        target_order.status = OrderStatus.FILLED

    elif status_str == "PARTIALLY_FILLED":
        target_order.status = OrderStatus.PARTIALLY_FILLED
        filled = data.get("filled_size")
        if filled is not None:
            target_order.filled_size = float(filled)
```

**ポイント: CANCELED → None にする理由**

キャンセルされた注文は板に存在しません。`None` にすることで、次の `_on_price_update` で自動的に再発注されます。

### _on_trade: 約定検知コールバック（厳格モード）

```python
async def _on_trade(self, data: dict[str, Any]) -> None:
    order_id = data.get("order_id")
    target_order, _side = self._match_order(order_id)
    if target_order is None:
        return

    logger.error("TRADE DETECTED — 約定は失敗。即座にクローズし Bot を停止します。")

    if self.risk_manager is not None:
        await self.risk_manager.close_position_immediately()

    self._exit_code = 1
    await self.shutdown()
```

**これが Bot の「最後の砦」です。**

```
約定検知 → RiskManager.close_position_immediately()
         → exit_code = 1
         → shutdown()
         → Bot停止
```

約定 = パラメータが甘い。Bot を停止し、人間がパラメータを見直してから再起動します。

### ヘルパー: _match_order

```python
def _match_order(self, order_id: str | None) -> tuple[Order | None, Side | None]:
    if order_id is not None:
        if self.bid_order is not None and self.bid_order.id == order_id:
            return self.bid_order, Side.BUY
        if self.ask_order is not None and self.ask_order.id == order_id:
            return self.ask_order, Side.SELL
    return None, None
```

WebSocketから受信した `order_id` が、現在管理中の BUY / SELL 注文のどちらに該当するかを特定します。不明な order_id は `(None, None)` を返して無視します。

---

## アクション実行の実装解説

### _evaluate_and_act: 評価からアクション実行まで

```python
async def _evaluate_and_act(self, order: Order, side: Side) -> None:
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
```

**ポイント: `finally` で in_flight を必ずリセット**

例外が発生しても `finally` でフラグがリセットされるため、次の価格更新で処理が再開されます。

### _execute_escape: 約定回避アクション

```python
async def _execute_escape(self, order: Order, side: Side) -> Order | None:
    escape_price = calculate_escape_price(
        self.mark_price, side, self.config.outer_escape_distance_bps
    )
    try:
        # 1. 外側にreposition（発注→キャンセルの順序）
        new_order = await self.order_manager.reposition_order(
            old_order_id=order.id,
            new_price=escape_price,
            side=side,
            size=self.config.order_size,
            strategy="place_first",
        )
        return new_order
    except APIError:
        # 2. reposition失敗 → キャンセルにフォールバック
        try:
            await self.order_manager.cancel_order(order.id)
        except APIError:
            logger.error("Escape cancel fallback also failed")
        return None
```

**ポイント: 2段階のフォールバック**

```
Step 1: reposition（新注文発注 → 旧注文キャンセル）
  → 成功: 板に常に存在（空白時間ゼロ）
  → 失敗 ↓

Step 2: cancel のみ
  → 成功: 板から消えるが約定は防げる
  → 失敗: ログ出力（次の価格更新で再試行）
```

**なぜ `strategy="place_first"` なのか？**

CLAUDE.md の設計原則「空白時間最小化」に基づいています。

```
✅ 発注 → キャンセル（板に常に注文が存在）
❌ キャンセル → 発注（板から消える時間が発生）
```

### _execute_reposition: 再配置アクション

```python
async def _execute_reposition(self, order: Order, side: Side) -> Order | None:
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
        return new_order
    except APIError:
        logger.error(f"Reposition failed for order_id={order.id}")
        return None
```

ESCAPE との違い：

| | ESCAPE | REPOSITION |
|---|--------|-----------|
| 目標価格 | `escape_price`（15bps外側） | `target_price`（8bps） |
| フォールバック | キャンセルにフォールバック | None を返す（次回再試行） |

ESCAPE ではキャンセルフォールバックがありますが、REPOSITION にはありません。理由は：

- **ESCAPE**: 約定リスクが高い → 何としてでも注文を移動/キャンセルする必要がある
- **REPOSITION**: 約定リスクは低い → 次の価格更新で再試行すれば十分

---

## ライフサイクル管理

### run(): メインループ

```python
async def run(self) -> None:
    async with StandXHTTPClient(self.config) as http_client:
        self.http_client = http_client
        self.order_manager = OrderManager(http_client, self.config)
        self.risk_manager = RiskManager(http_client, self.config)

        ws_client = StandXWebSocketClient(
            self.config, jwt_token=http_client.jwt_token
        )
        self.ws_client = ws_client

        ws_client.on_price_update(self._on_price_update)
        ws_client.on_order_update(self._on_order_update)
        ws_client.on_trade(self._on_trade)

        ws_task = asyncio.create_task(ws_client.connect())
        await self._shutdown_event.wait()
        await self._cleanup()
        ws_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ws_task
```

**起動フロー:**

```
1. HTTPクライアント作成（JWT認証も含む）
2. OrderManager / RiskManager 作成
3. WebSocketクライアント作成（JWT トークンを渡す）
4. コールバック登録
5. WebSocket接続を別タスクで開始
6. shutdown_event を待機（Botが動作中）
7. シャットダウン時: cleanup → WSタスクキャンセル
```

**ポイント: `asyncio.Event` による停止待機**

```python
await self._shutdown_event.wait()
```

`asyncio.Event` は `set()` されるまで待機します。SIGINT/SIGTERM または約定検知で `set()` されます。

**ポイント: `contextlib.suppress(asyncio.CancelledError)`**

```python
ws_task.cancel()
with contextlib.suppress(asyncio.CancelledError):
    await ws_task
```

`cancel()` はタスクに `CancelledError` を投げます。`await ws_task` でこの例外を回収しないとプログラムが不安定になります。`contextlib.suppress` で安全に無視します。

### shutdown(): グレースフルシャットダウン

```python
async def shutdown(self) -> None:
    if self._shutdown_event.is_set():
        return  # 冪等性: 2回呼んでも安全
    logger.info("Shutting down MakerStrategy...")
    self._shutdown_event.set()
```

**ポイント: 冪等性（idempotent）**

`shutdown()` は複数回呼ばれる可能性があります：

```
SIGINT → shutdown()
直後に SIGTERM → shutdown()  # 2回目は何もしない
```

### _cleanup(): 残注文キャンセル・WS切断

```python
async def _cleanup(self) -> None:
    if self.order_manager is not None:
        if self.bid_order is not None:
            try:
                await self.order_manager.cancel_order(self.bid_order.id)
            except Exception as e:
                logger.error(f"Failed to cancel BUY order: {e}")
        if self.ask_order is not None:
            try:
                await self.order_manager.cancel_order(self.ask_order.id)
            except Exception as e:
                logger.error(f"Failed to cancel SELL order: {e}")

    if self.ws_client is not None:
        await self.ws_client.disconnect()
```

**なぜクリーンアップが重要か？**

Bot停止時に注文が板に残ると、監視する人がいないまま約定するリスクがあります。停止前に必ず全注文をキャンセルします。

**ポイント: 例外を握りつぶす**

クリーンアップ中にAPIエラーが発生しても、処理を続行します。片方のキャンセルが失敗しても、もう片方とWS切断は実行します。

---

## エントリーポイント(__main__.py)の実装解説

```python
async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        config = Settings()
    except Exception as e:
        logger.error(f"Failed to load settings: {e}")
        sys.exit(1)

    strategy = MakerStrategy(config)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(
            sig, lambda: asyncio.create_task(strategy.shutdown())
        )

    try:
        await strategy.run()
    except Exception as e:
        logger.error(f"Bot error: {e}")
        await strategy.shutdown()
        sys.exit(1)

    sys.exit(strategy._exit_code)


if __name__ == "__main__":
    asyncio.run(main())
```

### シグナルハンドリング

```python
for sig in (signal.SIGINT, signal.SIGTERM):
    loop.add_signal_handler(sig, lambda: asyncio.create_task(strategy.shutdown()))
```

| シグナル | 発生タイミング | 対応 |
|---------|-------------|------|
| SIGINT | Ctrl+C | グレースフルシャットダウン |
| SIGTERM | `docker stop`, `kill` | グレースフルシャットダウン |

**なぜ `asyncio.create_task` でラップするのか？**

`add_signal_handler` のコールバックは同期関数である必要があります。`strategy.shutdown()` は `async` なので、`create_task` で非同期タスクとしてスケジュールします。

### 終了コード

| exit_code | 意味 |
|-----------|------|
| 0 | 正常終了（手動停止） |
| 1 | 異常終了（約定検知 or 起動失敗） |

```python
# 正常停止の場合
sys.exit(strategy._exit_code)  # → 0

# 約定検知の場合
self._exit_code = 1  # _on_trade()で設定
sys.exit(strategy._exit_code)  # → 1
```

---

## in_flightフラグによる重複防止

### なぜ必要か

WebSocketの価格更新は高頻度で到着します。前回のアクション（API呼び出し）が完了する前に次の価格更新が来ると、**同じ注文に対して重複操作**が発生します。

```
t=0: price更新 → evaluate → ESCAPE → API呼び出し開始
t=0.1: price更新 → evaluate → ESCAPE → 同じ注文に再度ESCAPE（重複！）
```

### 仕組み

```python
# 処理開始時にフラグを立てる
self._bid_in_flight = True

try:
    # API呼び出し（時間がかかる）
    action = self.evaluate_order(...)
    await self._execute_action(...)
finally:
    # 必ずリセット（例外時も）
    self._bid_in_flight = False
```

```
t=0:   price更新 → _bid_in_flight = True → ESCAPE実行中...
t=0.1: price更新 → _bid_in_flight = True → スキップ ✅
t=0.3: ESCAPE完了 → _bid_in_flight = False
t=0.4: price更新 → _bid_in_flight = False → 通常評価 ✅
```

### BUY と SELL は独立

```python
self._bid_in_flight: bool = False  # BUY専用
self._ask_in_flight: bool = False  # SELL専用
```

BUY のESCAPE中でも SELL の評価は実行できます。両サイドを独立して管理することで、片側の遅延がもう片側に影響しません。

---

## テストケース設計

Phase 4では、**モックテスト32件**を実装しました。

### テストクラスの構成

| テストクラス | テスト件数 | 検証対象 |
|------------|-----------|---------|
| `TestEvaluateOrder` | 7 | 判断ロジック（ESCAPE/REPOSITION/HOLD） |
| `TestPlaceInitialOrders` | 2 | 初期注文の発注 |
| `TestOnPriceUpdate` | 4 | 価格更新コールバック |
| `TestOnTrade` | 2 | 約定検知（厳格モード） |
| `TestOnOrderUpdate` | 4 | 注文ステータス更新 |
| `TestExecuteAction` | 5 | アクション実行 |
| `TestInFlight` | 4 | 重複防止フラグ |
| `TestShutdown` | 2 | シャットダウン |
| `TestCleanup` | 5 | クリーンアップ |

### テストの共通パターン

```python
@pytest.fixture
def config() -> Settings:
    return Settings(
        standx_private_key="0x" + "a" * 64,
        standx_wallet_address="0x1234567890abcdef",
        standx_chain="bsc",
        symbol="ETH-USD",
        order_size=0.001,
        target_distance_bps=8.0,
        escape_threshold_bps=3.0,
        outer_escape_distance_bps=15.0,
        reposition_threshold_bps=2.0,
        price_move_threshold_bps=5.0,
    )

def _make_strategy_with_mocks(config, mock_http_client):
    strategy = MakerStrategy(config)
    strategy.http_client = mock_http_client
    strategy.order_manager = OrderManager(mock_http_client, config)
    strategy.risk_manager = RiskManager(mock_http_client, config)
    return strategy
```

`_make_strategy_with_mocks` は `run()` を呼ばずにコンポーネントを注入するヘルパーです。テストでは WebSocket接続は不要なため、直接コンポーネントをセットします。

### 注目テストケース1: ESCAPEのフォールバック

```python
async def test_escape_failure_falls_back_to_cancel(self, config, mock_http_client):
    mock_http_client.new_order.side_effect = APIError("reposition failed")
    mock_http_client.cancel_order.return_value = {}
    strategy = _make_strategy_with_mocks(config, mock_http_client)
    strategy.mark_price = 2500.0
    order = _make_order(Side.BUY, 2498.0, "bid_001")

    result = await strategy._execute_action(Action.ESCAPE, order, Side.BUY)

    assert result is None
    mock_http_client.cancel_order.assert_called()  # フォールバック確認
```

reposition が失敗した場合に、cancel にフォールバックすることを検証。

### 注目テストケース2: 約定検知 → シャットダウン

```python
async def test_strict_mode_triggers_on_matching_order(self, config, mock_http_client):
    mock_http_client.get_position.return_value = []
    strategy = _make_strategy_with_mocks(config, mock_http_client)
    strategy.bid_order = _make_order(Side.BUY, 2498.0, "bid_001")

    await strategy._on_trade({"order_id": "bid_001"})

    assert strategy._exit_code == 1
    assert strategy._shutdown_event.is_set()
```

約定検知で `exit_code = 1` が設定され、シャットダウンイベントがセットされることを検証。

### 注目テストケース3: in_flightの例外安全性

```python
async def test_in_flight_reset_on_exception(self, config, mock_http_client):
    strategy = _make_strategy_with_mocks(config, mock_http_client)
    strategy.mark_price = 2500.0
    strategy.bid_order = _make_order(Side.BUY, 2499.5, "bid_001")

    mock_http_client.new_order.side_effect = APIError("fail")
    mock_http_client.cancel_order.side_effect = APIError("cancel fail")

    await strategy._evaluate_and_act(strategy.bid_order, Side.BUY)

    assert strategy._bid_in_flight is False  # finally でリセットされる
```

APIエラーが発生しても `_bid_in_flight` がリセットされることを検証。

---

## 設計判断の記録

### 判断1: WebSocket認証をHTTPクライアントのJWTトークンを流用

**理由:** 認証の一元管理。

```python
# HTTPクライアントが認証時に取得したJWTをWSに渡す
ws_client = StandXWebSocketClient(self.config, jwt_token=http_client.jwt_token)
```

**背景:**

StandX APIでは、order/tradeチャンネルの購読にJWT認証が必要です。HTTPクライアントが `__aenter__` で取得したJWTトークンをWebSocketクライアントに共有することで、認証を1回で済ませます。

### 判断2: evaluate_order() は同期メソッド

**理由:** 判断ロジックはI/Oを伴わないため、`async` にする必要がない。

```python
def evaluate_order(self, order, mark_price, side) -> Action:  # async なし
    # 純粋な計算のみ
```

**背景:**

`async` が必要なのはAPI呼び出しやI/Oを伴う処理のみ。計算だけのメソッドを `async` にすると、呼び出し側に不要な `await` を強制し、コードが複雑になります。

### 判断3: _exit_code による終了コードの伝播

**理由:** `sys.exit()` を直接呼ぶと、`_cleanup()` がスキップされる。

```python
# ❌ _on_trade内でsys.exit → クリーンアップされない
async def _on_trade(self, data):
    await self.risk_manager.close_position_immediately()
    sys.exit(1)  # _cleanup() が実行されない！

# ✅ exit_code を保存して shutdown → cleanup 後に sys.exit
async def _on_trade(self, data):
    await self.risk_manager.close_position_immediately()
    self._exit_code = 1
    await self.shutdown()  # → _cleanup() → sys.exit(1)
```

### 判断4: 片側注文のフォールバック差異

**理由:** リスクレベルが異なる。

| アクション | フォールバック | 理由 |
|-----------|-------------|------|
| ESCAPE | キャンセルにフォールバック | 約定リスクが高い → 何としてでも回避 |
| REPOSITION | None（次回再試行） | 約定リスクは低い → 急がない |

### 判断5: `_place_initial_orders` でBUY/SELLを個別にtry-except

**理由:** 片側の失敗がもう片側に影響しない。

```python
# BUY
try:
    self.bid_order = await self.order_manager.place_order(...)
except APIError:
    logger.error("Failed to place initial BUY order")

# SELL（BUYが失敗しても実行される）
try:
    self.ask_order = await self.order_manager.place_order(...)
except APIError:
    logger.error("Failed to place initial SELL order")
```

---

## まとめ

### Phase 4で実装した内容

| 項目 | 内容 |
|------|------|
| **モジュール** | `strategy/maker.py`, `__main__.py` |
| **クラス** | `MakerStrategy` |
| **判断ロジック** | `evaluate_order()` — ESCAPE / REPOSITION / HOLD |
| **コールバック** | `_on_price_update`, `_on_order_update`, `_on_trade` |
| **ライフサイクル** | `run()`, `shutdown()`, `_cleanup()` |
| **テスト** | モックテスト32件 |

### 重要なポイント

1. **evaluate_order() の優先順位**: ESCAPE > REPOSITION(境界) > REPOSITION(ドリフト) > HOLD
2. **空白時間最小化**: `strategy="place_first"` で発注→キャンセルの順序を守る
3. **厳格モード**: 約定検知 → 即クローズ → Bot停止
4. **in_flightフラグ**: 高頻度価格更新での重複操作を防止
5. **グレースフルシャットダウン**: SIGINT/SIGTERM → cleanup → 全注文キャンセル → WS切断

### 全Phase振り返り

```
Phase 1: 基盤
  ├─ config.py       : 設定管理（pydantic-settings）
  ├─ auth.py         : JWT認証（EVM署名）
  ├─ client/http.py  : REST APIクライアント
  └─ models.py       : データモデル

Phase 2: 通信
  └─ client/websocket.py : WebSocket接続・認証・チャンネル購読

Phase 3: コアロジック
  ├─ core/distance.py : 距離計算
  ├─ core/escape.py   : 約定回避価格計算
  ├─ core/order.py    : 注文管理（発注・キャンセル・再配置）
  └─ core/risk.py     : リスク管理（即座クローズ）

Phase 4: 統合
  ├─ strategy/maker.py : 全コンポーネント統合・判断ロジック
  └─ __main__.py       : エントリーポイント・シグナル処理
```

**これで Bot の全コンポーネントが実装され、動作可能な状態になりました。**

---

**実装PR**: [#71 Phase 4: 戦略統合の実装](https://github.com/zomians/standx_mm_bot/pull/71)

**前のチュートリアル**: [Phase 3-3: 厳格モード（リスク管理）の実装ガイド](./phase3-3-risk-management.md)
