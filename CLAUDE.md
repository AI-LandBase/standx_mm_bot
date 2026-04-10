# CLAUDE.md

Claude 向けクイックリファレンス。

---

## ドキュメント役割分担

| ドキュメント        | 役割                                                   |
| ------------------- | ------------------------------------------------------ |
| **README.md**       | プロジェクト概要、技術スタック、クイックスタート、トラブルシューティング |
| **CONTRIBUTING.md** | 開発規約（Issue、Git、コミット、PR、コーディング規約） |
| **CLAUDE.md**       | AI 向けクイックリファレンス（このファイル）            |
| **GUIDE.md**        | 理論的背景・技術基礎・設計思想の教科書的解説           |
| **DESIGN.md**       | Bot 実装設計書（アーキテクチャ・実装フェーズ・判断ロジック） |

**詳細な開発規約は [CONTRIBUTING.md](./CONTRIBUTING.md) を参照。**

---

## プロジェクト概要

**StandX MM Bot** - StandX の Maker Points / Maker Uptime を最大化する Market Making Bot

### 目的

**約定させずに板に居続けること**を自動化する。

```
約定 = 失敗
```

| 項目 | 方針 |
|------|------|
| 約定 | **しない**（手数料ゼロ、FRリスクゼロ） |
| 建玉 | **持たない**（清算リスクゼロ） |
| 距離 | 10bps以内だが約定しない位置 |

### 厳格モード

現実的には約定を完全に避けられない（価格急変、遅延）。

**対処**: 約定検知 → 即成行クローズ → Bot終了（パラメータ見直し後に手動再起動）

### 報酬条件

| 報酬 | 条件 | Bot の役割 |
|------|------|-----------|
| Maker Points | mark_price ± 10bps 以内、3秒以上 | 距離維持 |
| Maker Uptime | 両サイド ± 10bps、毎時30分以上 | 空白時間ゼロ |

### 技術スタック

- **Python 3.12+** / asyncio / aiohttp / websockets
- **pydantic-settings** (設定管理)
- **Ruff** (Lint/Format) / **mypy** (型チェック) / **pytest** (テスト)
- **対応チェーン**: BSC (デフォルト) / Solana

### アーキテクチャ概要

```
StandX MM Bot 構成

┌─────────────────────────────────────────┐
│          Main Application Loop           │
│  (src/standx_mm_bot/__main__.py)        │
└────────────┬────────────────────────────┘
             │
    ┌────────┴────────┐
    │                 │
    ▼                 ▼
┌─────────┐      ┌──────────┐
│ REST    │      │WebSocket │
│ API     │      │ Client   │
│ Client  │      │          │
└────┬────┘      └────┬─────┘
     │                │
     │                ├─ price channel (mark_price)
     │                └─ order channel (order status)
     │
     ▼
┌──────────────────────────────────────────┐
│         Core Logic                        │
├──────────────────────────────────────────┤
│ ┌──────────────┐  ┌─────────────────┐   │
│ │ Order        │  │ Escape Logic    │   │
│ │ Management   │  │ (約定回避)       │   │
│ └──────────────┘  └─────────────────┘   │
│                                          │
│ ┌──────────────────────────────────┐   │
│ │ Position Monitor (建玉ゼロ維持)  │   │
│ └──────────────────────────────────┘   │
└──────────────────────────────────────────┘
     │
     ▼
┌──────────────────────────────────────────┐
│  Strategy Layer                           │
│  (src/standx_mm_bot/strategy/maker.py)   │
└──────────────────────────────────────────┘
```

---

## ディレクトリ構成

```
standx_mm_bot/
├── src/standx_mm_bot/
│   ├── __main__.py        # エントリーポイント
│   ├── config.py          # 設定管理
│   ├── auth.py            # JWT認証
│   ├── models.py          # データモデル (Order, Side, Action等)
│   ├── client/
│   │   ├── exceptions.py  # カスタム例外
│   │   ├── http.py        # REST API
│   │   └── websocket.py   # WebSocket
│   ├── core/
│   │   ├── distance.py    # bps距離計算
│   │   ├── escape.py      # 約定回避ロジック
│   │   ├── order.py       # 注文管理 (OrderManager)
│   │   └── risk.py        # 厳格モード (RiskManager)
│   └── strategy/
│       └── maker.py       # Maker戦略 (全体統合)
├── tests/
├── pyproject.toml
└── Makefile
```

---

## よく使うコマンド

```bash
# ウォレット作成（初回のみ）
make wallet          # BSC (EVM) ウォレット作成（デフォルト）
make wallet-bsc      # BSC (EVM) ウォレット作成
make wallet-solana   # Solana ウォレット作成

# 起動
make up

# ETH-USDで起動
make up-eth

# BTC-USDで起動
make up-btc

# シンボル切り替え
make switch-eth  # ETH-USD
make switch-btc  # BTC-USD

# 設定確認
make config

# テスト
make test

# 型チェック + Lint + テスト
make check

# フォーマット
make format
```

---

## コミット規約

```bash
# フォーマット
<type>(<scope>): <subject> (issue#XX)

# 例
feat(ws): WebSocket接続を実装 (issue#2)
fix(escape): 約定回避ロジックを修正 (issue#3)
```

**Scope**: `auth`, `ws`, `order`, `escape`, `config`, `docs`

**禁止事項**:
- main ブランチへの直接プッシュ
- issue 番号なしのコミット

---

## CONTRIBUTING.md 活用方法

開発作業の流れは CONTRIBUTING.md に詳細に記載されています。

### 開発ワークフロー（5 ステップ）

```bash
# 1. Issue 作成（GitHub で実施）
# → CONTRIBUTING.md の「Issue 作成ガイドライン」参照

# 2. ブランチ作成
git checkout main && git pull origin main
git checkout -b feature/XX-description

# 3. 実装
# → CONTRIBUTING.md の「コーディング規約」参照

# 4. コミット
git add .
git commit -m "feat(scope): 変更内容 (issue#XX)"

# 5. PR 作成
gh pr create --title "タイトル" --body "Closes #XX"
```

### コミット規約

```bash
# フォーマット
<type>(<scope>): <subject> (issue#XX)

# 例
feat(ws): WebSocket接続を実装 (issue#2)
fix(escape): 約定回避ロジックを修正 (issue#3)
docs: CONTRIBUTING.mdを追加 (issue#4)
```

**Scope**: `auth`, `ws`, `order`, `escape`, `config`, `docs`

**禁止事項**:

- ❌ main ブランチへの直接プッシュ
- ❌ issue 番号なしのコミット
- ❌ AI ツール署名の追加（`Co-Authored-By: Claude` など）

---

## 重要な設計原則（プロジェクト固有）

### 1. 約定回避が最優先

```python
# ✅ GOOD: 価格が近づいたら即座にキャンセル
if distance_to_order < ESCAPE_THRESHOLD_BPS:  # 3bps
    await cancel_order(order_id)

# ❌ BAD: 約定を許容
if distance_to_order < 1:  # 遅すぎる
    await cancel_order(order_id)
```

### 2. 建玉ゼロ維持

```python
# ✅ GOOD: 約定検知時は即クローズ
if order.status == "FILLED":
    await close_position_immediately()

# ❌ BAD: 建玉を放置
if order.status == "FILLED":
    logger.warning("Filled")  # 何もしない
```

### 3. 空白時間最小化

```python
# ✅ GOOD: 新規注文発注 → 確認後、旧注文キャンセル
new_order = await place_order(new_price)
if new_order.status == "OPEN":
    await cancel_order(old_order_id)

# ❌ BAD: キャンセル → 発注（板から消える時間が発生）
await cancel_order(old_order_id)
await place_order(new_price)
```

---

## セキュリティチェック

実装時に以下を確認してください：

```python
# 1. 秘密鍵管理
# ✅ DO: 環境変数から取得
import os
PRIVATE_KEY = os.environ["STANDX_PRIVATE_KEY"]

# ❌ DON'T: ハードコード
PRIVATE_KEY = "0x1234..."  # 絶対NG

# 2. ログ出力の注意
# ✅ DO: 秘密情報をマスク
logger.info(f"Wallet: {address[:6]}...{address[-4:]}")

# ❌ DON'T: 秘密情報をそのまま出力
logger.info(f"Private key: {private_key}")  # 絶対NG

# 3. .env ファイルの管理
# - .env は絶対にコミットしない
# - .gitignore に追加されていることを確認
```

---

## API クイックリファレンス

### Base URL

```
REST: https://perps.standx.com
WS: wss://perps.standx.com/ws-stream/v1
```

### 主要エンドポイント

| 用途 | パス |
|------|------|
| 価格取得 | `GET /api/query_symbol_price?symbol=ETH_USDC` |
| 注文発注 | `POST /api/new_order` |
| 注文キャンセル | `POST /api/cancel_order` |
| 未決注文 | `GET /api/query_open_orders` |

### WebSocket チャンネル

| チャンネル | 認証 | 用途 |
|-----------|------|------|
| `price` | 不要 | mark_price |
| `order` | 必要 | 注文状態 |
| `depth_book` | 不要 | 板情報（約定回避判断用） |

---

## Bot ロジック要点

### 設計思想

```
価格が近づいてきたら → 逃げる
価格が離れたら → 戻る
```

### bps 計算

```python
distance_bps = abs(order_price - mark_price) / mark_price * 10000
```

### 約定回避ロジック

```python
# 価格が注文に近づいたら逃げる
if distance_to_order < ESCAPE_THRESHOLD_BPS:  # 3bps
    cancel_order() or move_to_outer(15bps)

# 価格が離れたら戻る
if distance_to_order > RETURN_THRESHOLD_BPS:
    move_to_target(8bps)
```

### 優先順位

1. **約定回避** — 最優先。建玉を持たない
2. **10bps維持** — Uptime条件を満たす
3. **空白時間最小化** — 板に常に存在

### 空白時間最小化

```
1. 新価格で新規注文発注
2. 確認後、旧注文キャンセル
```

キャンセル→発注ではなく、発注→キャンセルの順序。

---

## Claude Code 向けワークフロー

### 推奨プロンプト形式

```
issue#XX の [タスク内容] を実装してください。

要件:
- [要件1]
- [要件2]
```

### 作業手順

1. **Issue 確認**: `gh issue view XX`
2. **ブランチ作成**: `git checkout -b feature/XX-description`
3. **実装**: コーディング規約に従う
4. **テスト追加**: pytest でテスト作成
5. **コミット**: Conventional Commits 形式
6. **PR 作成**: `gh pr create`

---

## 自動実行ポリシー

Claude Code が操作を実行する際の基準を定義します。

### 確認なしで実行可能

以下の**読み取り専用操作**は、確認なしで自動実行できます：

- **ファイル読み取り**: Read, Grep, Glob
- **状態確認コマンド**:
  - `git status`, `git log`, `git diff`
  - `ls`, `cat`, `grep`, `find`
- **テスト実行**:
  - `pytest`
  - `make test`
- **型チェック・Lint**:
  - `mypy .`
  - `ruff check .`

### 必ず確認が必要

以下の**書き込み・変更操作**は、ユーザーの明示的な承認が必要です：

- **Git 操作**:
  - `git commit`, `git push`
  - `git branch` 作成・削除
  - `git merge`, `git rebase`
- **ファイル削除・上書き**:
  - Write, Edit（既存ファイルの上書き）
  - `rm`, `mv`（ファイル削除・移動）
- **依存関係の変更**:
  - `pip install`
  - `pyproject.toml` の変更
- **本番環境への操作**:
  - デプロイコマンド
  - 環境変数の変更
  - 本番APIへのアクセス

**原則**: データの永続的な変更や、元に戻せない操作は必ず確認する。

---

## 関連リンク

- [README.md](./README.md) - プロジェクト概要、クイックスタート
- [CONTRIBUTING.md](./CONTRIBUTING.md) - 開発規約の詳細
- [StandX API Docs](https://docs.standx.com/standx-api/standx-api)

---

**Last Updated**: 2026-04-10
