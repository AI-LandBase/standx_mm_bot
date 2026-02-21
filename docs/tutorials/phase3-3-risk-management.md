# Phase 3-3: 厳格モード（リスク管理）の実装ガイド

このガイドでは、Phase 3-3で実装した厳格モード（`core/risk.py`）について詳しく解説します。

**対象読者**: Python初心者、非同期プログラミング初心者、Market Making初心者

**関連Issue**: [#18 Phase 3-3: 厳格モードの実装](https://github.com/zomians/standx_mm_bot/issues/18)

**実装PR**: [#67 Phase 3-3: 厳格モードの実装](https://github.com/zomians/standx_mm_bot/pull/67)

---

## 目次

1. [概要](#概要)
2. [なぜ厳格モードが必要なのか](#なぜ厳格モードが必要なのか)
3. [ポジションクローズの仕組み](#ポジションクローズの仕組み)
4. [RiskManager.close_position_immediately()の実装解説](#riskmanagerclose_position_immediatelyの実装解説)
5. [RiskManager._parse_position_response()の実装解説](#riskmanager_parse_position_responseの実装解説)
6. [リトライロジック](#リトライロジック)
7. [テストケース設計](#テストケース設計)
8. [設計判断の記録](#設計判断の記録)
9. [まとめ](#まとめ)

---

## 概要

### Phase 3-3の目的

Phase 3-1（約定回避ロジック）と Phase 3-2（注文管理）は、**約定を防ぐ**仕組みです。しかし、価格急変やネットワーク遅延により、完全に約定を避けることは不可能です。

Phase 3-3は、万が一約定してしまった場合の**フェイルセーフ**です。

```
Phase 3-1 (escape.py):  価格が近づいたら逃げる（予防）
Phase 3-2 (order.py):   注文を管理する（運用）
Phase 3-3 (risk.py):    約定したら即クローズ（事後対応） ← 今回
```

### 実装したモジュール

| モジュール | 責務 | メソッド数 |
|-----------|------|-----------|
| `core/risk.py` | 約定時の即座ポジションクローズ | 2 |

### なぜ重要なのか

```
約定 = 失敗
建玉 = リスク
```

このBotの目的は**取引で利益を出すこと**ではなく、**板に居続けて Maker Points / Uptime 報酬をもらうこと**です。ポジションを持ってしまうと、以下のリスクに曝されます：

| リスク | 説明 |
|--------|------|
| 価格変動リスク | ポジション保有中に価格が不利な方向に動くと損失 |
| Funding Rate | ポジション保有中、定期的にFR（資金調達率）を支払う可能性 |
| 清算リスク | 大幅な価格変動でポジションが強制決済される可能性 |

**厳格モードは、これらのリスク曝露を最小限（数秒）に抑えるための仕組みです。**

---

## なぜ厳格モードが必要なのか

### 約定回避ロジックの限界

Phase 3-1で実装した約定回避ロジックは、以下の状況で約定を防げません：

| 状況 | 説明 |
|------|------|
| 価格の急変 | 一瞬で大きく動いた場合、escape判定が間に合わない |
| ネットワーク遅延 | キャンセルAPIのレスポンスが遅延し、その間に約定 |
| WebSocket切断 | 価格更新が途絶え、判断できない状態で約定 |

### 「反対側で逃げ続ける」ではダメな理由

「BUY注文が約定したなら、SELL注文を出して逃げ続ければ良いのでは？」と思うかもしれません。しかし、これは以下の理由で採用しません。

#### BUYポジションを持ったままSELL注文で逃げ続ける場合

```
BUY約定 → SELLで逃げ続ける
  → ポジション保有中ずっとリスクに曝される
  → SELL注文もいつか約定する可能性
  → そのSELL約定でちょうど±0になる保証はない
  → 無限ループのリスク
```

#### 成行で即クローズする場合

```
BUY約定 → SELL成行で即クローズ → Bot停止
  → リスク曝露は数秒だけ
  → 確実にポジションゼロに戻る
  → パラメータを見直して手動再起動
```

**結論**: ポジションは1秒でも早く消すのが正解。約定した＝パラメータが甘い＝見直しが必要→Bot停止。

---

## ポジションクローズの仕組み

### デリバティブ取引でのクローズ方法

デリバティブ（先物）取引では、ポジションを閉じるには**反対方向の注文を出す**必要があります。

```
BUYポジションを持っている → SELLで閉じる
SELLポジションを持っている → BUYで閉じる
```

### 指値と成行の選択

| 注文タイプ | 特徴 | 使う場面 |
|-----------|------|---------|
| 指値 (Limit) | 指定価格で約定を待つ | 通常のMM注文（板に並ぶ） |
| **成行 (Market)** | **今すぐ最良価格で約定** | **緊急クローズ（待てない）** |

このBotでは「約定 = 失敗」なので、ポジションを持ってしまったら**一刻も早く手放す**必要があります。指値だと約定するまで時間がかかり、その間にリスクに曝され続けます。

### reduce_only フラグ

`reduce_only=True` は「ポジションを減らす方向の注文のみ許可」する安全フラグです。

```python
await self.client.new_order(
    # ...
    reduce_only=True,  # ← 安全フラグ
)
```

**効果:**
- BUYポジション持ち → SELLの`reduce_only`は通る
- ポジションなし → 注文が拒否される（新規ポジション作成を防ぐ）

### 具体例

```
1. BUY注文 (price=2500, size=0.001) が約定してしまった
   → 0.001 ETH のBUYポジションを保有

2. 即座に SELL 成行注文 (size=0.001, reduce_only=True) を出す
   → 現在の最良価格で即座に約定
   → ポジション = 0 に戻る

3. Bot終了（パラメータ見直し後に手動再起動）
```

---

## RiskManager.close_position_immediately()の実装解説

### 責務

ポジションを成行注文で即座にクローズし、ゼロになったことを確認する。

### シグネチャ

```python
async def close_position_immediately(self) -> bool:
    """
    成行でポジションを即座にクローズ（リトライ付き）.

    Returns:
        bool: ポジションがゼロになったら True、リトライ上限到達で False
    """
```

### 実装の流れ

```python
async def close_position_immediately(self) -> bool:
    # 1. asyncio.Lockで排他制御
    async with self._lock:
        for attempt in range(1, MAX_RETRIES + 1):
            # 2. 現在のポジションを取得
            response = await self.client.get_position(self.config.symbol)
            position = self._parse_position_response(response)

            # 3. ポジションなし → 成功
            if position is None:
                logger.info("No position to close")
                return True

            # 4. 反対サイドを決定
            close_side = Side.SELL if position.side == Side.BUY else Side.BUY

            # 5. 成行注文でクローズ
            logger.error(
                f"Closing position immediately (attempt {attempt}/{MAX_RETRIES}): "
                f"side={position.side.value}, size={position.size}, ..."
            )

            await self.client.new_order(
                symbol=self.config.symbol,
                side=close_side.value.lower(),
                price=0,              # 成行注文は価格不要
                size=position.size,
                order_type="market",   # ← 成行
                time_in_force="ioc",   # ← Immediate Or Cancel
                reduce_only=True,      # ← 安全フラグ
            )

            # 6. ポジションゼロを確認
            verify_response = await self.client.get_position(self.config.symbol)
            verify_position = self._parse_position_response(verify_response)

            if verify_position is None:
                logger.info("Position closed successfully")
                return True

            # 7. まだ残っていればリトライ
            logger.warning(f"Position still exists after close attempt {attempt}: ...")

            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_INTERVAL_SEC)

        # 8. リトライ上限到達
        logger.error(f"Failed to close position after {MAX_RETRIES} attempts")
        return False
```

### ポイント1: time_in_force="ioc"

```python
time_in_force="ioc"  # Immediate Or Cancel
```

成行注文には `ioc`（Immediate Or Cancel）を指定します。

| time_in_force | 意味 | 成行注文での使用 |
|---------------|------|----------------|
| `gtc` | Good Til Canceled | 板に残る可能性がある |
| `alo` | Add Liquidity Only | 成行注文には使えない |
| **`ioc`** | **即座に約定、残りはキャンセル** | **成行注文に最適** |

`ioc` を使うことで、約定できなかった分が板に残り続けることを防ぎます。

### ポイント2: price=0

```python
price=0,  # 成行注文は価格不要
```

成行注文は「現在の最良価格で即座に約定」するため、価格の指定は不要です。API仕様上、パラメータとして渡す必要がありますが、`0` を指定します。

### ポイント3: ログレベルの使い分け

```python
# 約定クローズ = 異常事態 → logger.error
logger.error(f"Closing position immediately ...")

# 成功 = 正常復帰 → logger.info
logger.info("Position closed successfully")

# リトライ中 = 注意 → logger.warning
logger.warning(f"Position still exists after close attempt ...")
```

約定自体がこのBotにとっての**異常事態**なので、クローズ操作のログは `error` レベルで出力します。

---

## RiskManager._parse_position_response()の実装解説

### 責務

`get_position()` APIのレスポンスを `Position` データクラスに変換する。

### なぜパーサーが必要か

StandX APIのレスポンス形式は複数パターンがあります：

```python
# パターン1: dict形式
{"side": "BUY", "size": 0.001, "entry_price": 2500.0, "unrealized_pnl": -0.5}

# パターン2: リスト形式（ポジションあり）
[{"side": "BUY", "size": 0.001, "entry_price": 2500.0}]

# パターン3: 空リスト（ポジションなし）
[]

# パターン4: size=0（ポジションなし）
{"side": "BUY", "size": 0, "entry_price": 0}
```

### 実装の流れ

```python
def _parse_position_response(self, response: dict[str, Any]) -> Position | None:
    # 1. リスト形式の場合
    if isinstance(response, list):
        if len(response) == 0:
            return None  # ポジションなし
        response = response[0]  # 最初の要素を使用

    # 2. size=0チェック
    size = float(response.get("size", 0))
    if size == 0:
        return None  # ポジションなし

    # 3. side のパース
    side_str = response.get("side", "").upper()
    try:
        side = Side(side_str)
    except ValueError:
        logger.warning(f"Unknown position side: {side_str}")
        return None  # 不明なside → 安全のためNone

    # 4. Positionオブジェクトを生成
    return Position(
        symbol=response.get("symbol", self.config.symbol),
        side=side,
        size=size,
        entry_price=float(response.get("entry_price", 0)),
        unrealized_pnl=float(response.get("unrealized_pnl", 0)),
    )
```

### ポイント: 防御的パース

```python
# float変換でKeyError/TypeErrorを防ぐ
size = float(response.get("size", 0))  # キーがなくても0を返す

# 不明なsideは安全のためNone
try:
    side = Side(side_str)
except ValueError:
    return None  # パース失敗 → ポジションなしとして扱う
```

APIレスポンスの形式が変わった場合でも、クラッシュせずに安全に動作するように**防御的にパース**しています。

---

## リトライロジック

### なぜリトライが必要か

成行注文を出しても、以下の理由でポジションが即座にゼロにならない場合があります：

| 理由 | 説明 |
|------|------|
| 板の流動性不足 | 成行注文に対して十分な流動性がなく、一部のみ約定 |
| API処理の遅延 | ポジション反映に時間がかかる |
| ネットワーク遅延 | 確認リクエストが先に到着し、まだ反映されていない |

### リトライ設定

```python
MAX_RETRIES = 3           # 最大3回試行
RETRY_INTERVAL_SEC = 0.5  # 0.5秒間隔
```

### タイムライン

```
attempt 1:
  get_position() → ポジションあり
  new_order(market) → 成行クローズ
  get_position() → まだ残存
  sleep(0.5)

attempt 2:
  get_position() → ポジションあり
  new_order(market) → 成行クローズ
  get_position() → クローズ成功！ → return True

最悪ケース:
  attempt 1 → 残存
  attempt 2 → 残存
  attempt 3 → 残存 → return False
  → 合計所要時間: 約1〜2秒 + API呼び出し時間
```

### なぜ3回なのか

| 回数 | 所要時間 | 十分か |
|------|---------|--------|
| 1回 | ~0.5秒 | 流動性不足時に失敗する可能性 |
| **3回** | **~1.5秒** | **ほとんどのケースをカバー** |
| 10回 | ~5秒 | リスク曝露時間が長すぎる |

**トレードオフ**: リトライ回数を増やすと成功率は上がるが、リスク曝露時間も長くなる。3回はこのバランスの最適値です。

---

## テストケース設計

Phase 3-3では、**モックテスト12件**を実装しました。

### テストクラスの構成

| テストクラス | テスト件数 | 検証対象 |
|------------|-----------|---------|
| `TestClosePositionImmediately` | 6 | クローズロジック全体 |
| `TestParsePositionResponse` | 5 | レスポンスパース |
| `TestConcurrency` | 1 | 並行処理の安全性 |

### モックの設計

```python
@pytest.fixture
def mock_client() -> Mock:
    """モックHTTPクライアント."""
    client = Mock(spec=StandXHTTPClient)
    client.get_position = AsyncMock()  # ポジション取得
    client.new_order = AsyncMock()     # 成行注文
    return client
```

### テストケース1: ポジションなしの場合

```python
async def test_no_position_returns_true(self, mock_client, config):
    """ポジションなしの場合、True を返すことを確認."""
    mock_client.get_position.return_value = []  # 空リスト

    risk_mgr = RiskManager(mock_client, config)
    result = await risk_mgr.close_position_immediately()

    assert result is True
    mock_client.new_order.assert_not_called()  # 成行注文は出さない
```

**検証ポイント**: ポジションがなければ何もせず成功を返す。

### テストケース2: BUYポジションのクローズ

```python
async def test_close_buy_position(self, mock_client, config):
    """BUYポジションをSELL成行でクローズすることを確認."""
    mock_client.get_position.side_effect = [
        {"side": "BUY", "size": 0.001, "entry_price": 2500.0},  # ポジションあり
        [],  # クローズ確認 → 成功
    ]
    mock_client.new_order.return_value = {"order_id": "close1", "status": "FILLED"}

    risk_mgr = RiskManager(mock_client, config)
    result = await risk_mgr.close_position_immediately()

    assert result is True
    mock_client.new_order.assert_called_once_with(
        symbol="ETH-USD",
        side="sell",            # ← BUYの反対
        price=0,
        size=0.001,
        order_type="market",    # ← 成行
        time_in_force="ioc",
        reduce_only=True,       # ← 安全フラグ
    )
```

**検証ポイント**: BUYポジションに対してSELL成行注文が出されること。

### テストケース3: リトライの検証

```python
async def test_retry_on_position_remaining(self, mock_client, config):
    """クローズ後もポジション残存時にリトライすることを確認."""
    position_data = {"side": "BUY", "size": 0.001, "entry_price": 2500.0}

    mock_client.get_position.side_effect = [
        position_data,  # 1回目: ポジションあり
        position_data,  # 1回目確認: まだ残存
        position_data,  # 2回目: ポジションあり
        [],             # 2回目確認: クローズ成功
    ]

    with patch("standx_mm_bot.core.risk.asyncio.sleep", new_callable=AsyncMock):
        risk_mgr = RiskManager(mock_client, config)
        result = await risk_mgr.close_position_immediately()

    assert result is True
    assert mock_client.new_order.call_count == 2  # 2回試行
```

**検証ポイント**:

1. `side_effect` でAPI呼び出しごとに異なるレスポンスを返す
2. `asyncio.sleep` をモックして待機時間をスキップ
3. `new_order` が2回呼ばれたことを確認

### テストケース4: リトライ上限到達

```python
async def test_max_retries_exceeded(self, mock_client, config):
    """リトライ上限到達時に False を返すことを確認."""
    position_data = {"side": "BUY", "size": 0.001, "entry_price": 2500.0}

    # 全てのリトライでポジション残存
    mock_client.get_position.return_value = position_data

    with patch("standx_mm_bot.core.risk.asyncio.sleep", new_callable=AsyncMock):
        risk_mgr = RiskManager(mock_client, config)
        result = await risk_mgr.close_position_immediately()

    assert result is False
    assert mock_client.new_order.call_count == 3  # MAX_RETRIES = 3
```

**検証ポイント**: 全リトライで失敗した場合、`False` を返すこと。

### テストでの asyncio.sleep のモック

```python
with patch("standx_mm_bot.core.risk.asyncio.sleep", new_callable=AsyncMock):
    # asyncio.sleepが即座に完了する
    result = await risk_mgr.close_position_immediately()
```

**なぜモックするのか？**

テストで0.5秒×2回 = 1秒の待機は不要です。`asyncio.sleep` をモックすることで、テストを高速化します。

---

## 設計判断の記録

### 判断1: RiskManagerはクローズのみを担当し、Bot終了は行わない

**理由:** 責務の分離（Single Responsibility Principle）

```python
# RiskManagerの責務
async def close_position_immediately(self) -> bool:
    # ポジションクローズのみ
    # → 成功/失敗を返す

# Bot終了は呼び出し側（Phase 4: strategy/maker.py）の責務
if not await risk_manager.close_position_immediately():
    logger.error("Failed to close position")
sys.exit(1)  # ← ここはstrategy層の責務
```

**背景:**

DESIGN.mdでは `on_trade()` → `close_position_immediately()` → `sys.exit(1)` の流れが示されていますが、`sys.exit(1)` は戦略統合（Phase 4）で実装します。RiskManagerは「クローズを試みて結果を返す」ことに集中させます。

### 判断2: reduce_only=True を使用

**理由:** 万が一ポジションが既に消えていた場合の安全策。

```python
reduce_only=True  # ← ポジションがなければ注文が拒否される
```

**背景:**

`reduce_only=False` だと、ポジションがない状態でSELL成行注文を出した場合、新しいSELLポジションが作られてしまいます。`reduce_only=True` にすることで、このリスクを排除します。

### 判断3: リトライ間隔は0.5秒

**理由:** 早すぎず遅すぎないバランス。

| 間隔 | メリット | デメリット |
|------|---------|-----------|
| 0.1秒 | 高速リトライ | API負荷、まだ反映されていない可能性 |
| **0.5秒** | **適度な間隔** | **ほとんどのケースで反映済み** |
| 2秒 | 確実に反映 | リスク曝露時間が長い |

### 判断4: 戻り値は bool

**理由:** シンプルなインターフェース。

```python
# ✅ 採用: bool
async def close_position_immediately(self) -> bool:
    # True: 成功, False: 失敗

# ❌ 不採用: 例外
async def close_position_immediately(self) -> None:
    # 失敗時に例外を投げる
    raise PositionCloseError("Failed to close position")
```

**背景:**

呼び出し側（Phase 4）は成功/失敗に応じて後続処理を変える必要があります。例外よりも戻り値の方が制御フローが明確です。

### 判断5: 型アノテーションで `dict[str, Any]` ではなく `Position | None` を使用

**理由:** 型安全性の向上。

```python
# パースメソッドでdict → Positionに変換
def _parse_position_response(self, response: dict[str, Any]) -> Position | None:
    # ...
    return Position(symbol=..., side=..., size=..., entry_price=...)
```

**背景:**

OrderManagerの `_parse_order_response()` と同じパターン。APIレスポンス（dict）をドメインモデル（Position）に変換することで、型チェッカー（mypy）の恩恵を受けられます。

---

## まとめ

### Phase 3-3で実装した内容

| 項目 | 内容 |
|------|------|
| **モジュール** | `core/risk.py` |
| **クラス** | `RiskManager` |
| **メソッド** | `close_position_immediately()`, `_parse_position_response()` |
| **重要概念** | 成行注文、reduce_only、リトライロジック |
| **テスト** | モックテスト12件（カバレッジ100%） |

### 重要なポイント

1. **約定 = 失敗**: ポジションは1秒でも早く消す
2. **成行注文 (market + ioc)**: 即座に最良価格で約定させる
3. **reduce_only=True**: 新規ポジション作成を防ぐ安全フラグ
4. **リトライ（最大3回）**: 流動性不足やAPI遅延に対応
5. **責務の分離**: クローズのみ担当、Bot終了は呼び出し側

### Phase 3 全体の振り返り

```
Phase 3-1 (escape.py):  予防   → 価格が近づいたら逃げる
Phase 3-2 (order.py):   運用   → 注文を管理する（発注・キャンセル・再配置）
Phase 3-3 (risk.py):    事後   → 約定したら即クローズ
```

これで Phase 3（コアロジック）が完了しました。

### 次のステップ

**Phase 4**: 戦略統合（`strategy/maker.py` + `__main__.py`）

- WebSocketコールバックと注文管理を統合
- 価格更新時の判断ロジック（ESCAPE / REPOSITION / HOLD）
- 約定検知時のRiskManager呼び出し → Bot終了

---

**実装PR**: [#67 Phase 3-3: 厳格モードの実装](https://github.com/zomians/standx_mm_bot/pull/67)

**前のチュートリアル**: [Phase 3-2: 注文管理の実装ガイド](./phase3-2-order-management.md)
