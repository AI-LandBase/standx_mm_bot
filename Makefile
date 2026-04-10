.DEFAULT_GOAL := help
.PHONY: help up down test test-integration test-all test-cov typecheck lint format format-check check logs clean build-prod up-prod restart-prod wallet wallet-bsc wallet-solana price orders position balance status switch-eth switch-btc up-eth up-btc config

## Bot
up:            ## Bot 起動
	docker compose up -d

down:          ## Bot 停止
	docker compose down

logs:          ## ログ確認
	docker compose logs -f bot

up-eth: switch-eth up  ## ETH-USD で起動
up-btc: switch-btc up  ## BTC-USD で起動

## テスト
test:          ## テスト実行（統合テスト除外）
	docker compose run --rm bot pytest -m "not integration"

test-integration:  ## 統合テスト（実API使用）
	docker compose run --rm bot pytest -m integration

test-all:      ## 全テスト（統合テスト含む）
	docker compose run --rm bot pytest

test-cov:      ## テスト + カバレッジ
	docker compose run --rm bot pytest -m "not integration" --cov --cov-report=term-missing

## コード品質
typecheck:     ## 型チェック (mypy)
	docker compose run --rm bot mypy src

lint:          ## Lint (ruff)
	docker compose run --rm bot ruff check src tests

format:        ## フォーマット (ruff)
	docker compose run --rm bot ruff format src tests
	docker compose run --rm bot ruff check --fix src tests

format-check:  ## フォーマットチェック（修正なし）
	docker compose run --rm bot ruff format --check src tests

check: format-check lint typecheck test-cov  ## 全チェック (format + lint + typecheck + test)

## ツール
wallet: wallet-bsc  ## ウォレット作成（デフォルト: BSC）

wallet-bsc:    ## BSC ウォレット作成
	@echo "Creating new BSC wallet and generating .env file..."
	docker compose run --rm bot python scripts/create_wallet_bsc.py

wallet-solana: ## Solana ウォレット作成
	@echo "Creating new Solana wallet and generating .env file..."
	docker compose run --rm bot python scripts/create_wallet_solana.py

price:         ## 現在の価格を取得
	@echo "Fetching current price..."
	docker compose run --rm bot python scripts/read_api.py price

orders:        ## 未決注文を取得
	@echo "Fetching open orders..."
	docker compose run --rm bot python scripts/read_api.py orders

position:      ## 現在のポジションを取得
	@echo "Fetching current position..."
	docker compose run --rm bot python scripts/read_api.py position

balance:       ## 残高を取得（StandX + チェーン）
	@echo "Fetching balance (StandX + Chain)..."
	docker compose run --rm bot python scripts/read_api.py balance

status:        ## 全ステータスを取得
	@echo "Fetching all status..."
	docker compose run --rm bot python scripts/read_api.py status

config:        ## 現在の設定確認（秘密鍵は非表示）
	@echo "=== Current Configuration ==="
	@if [ -f .env ]; then \
		cat .env | grep -v "KEY" | grep -v "^#" | grep -v "^$$"; \
	else \
		echo "Error: .env file not found"; \
	fi

## シンボル切り替え
switch-eth:    ## ETH-USD に切り替え
	@if [ ! -f .env ]; then echo "Error: .env file not found. Run 'cp .env.example .env' first."; exit 1; fi
	@sed -i.bak 's/^SYMBOL=.*/SYMBOL=ETH-USD/' .env && rm -f .env.bak
	@echo "Switched to ETH-USD"
	@grep "^SYMBOL=" .env

switch-btc:    ## BTC-USD に切り替え
	@if [ ! -f .env ]; then echo "Error: .env file not found. Run 'cp .env.example .env' first."; exit 1; fi
	@sed -i.bak 's/^SYMBOL=.*/SYMBOL=BTC-USD/' .env && rm -f .env.bak
	@echo "Switched to BTC-USD"
	@grep "^SYMBOL=" .env

## 本番環境
build-prod:    ## 本番イメージビルド
	docker compose -f compose.prod.yaml build

up-prod:       ## 本番 Bot 起動
	docker compose -f compose.prod.yaml up -d

restart-prod:  ## 本番 Bot 再起動
	docker compose -f compose.prod.yaml restart

## クリーンアップ
clean:         ## キャッシュ・一時ファイル削除
	docker compose down -v
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

## ヘルプ
help:          ## このヘルプを表示
	@echo "Usage: make [target]"
	@echo ""
	@awk '/^## [^#]/{if(NR>1)printf "\n"; printf "\033[1m%s\033[0m\n", substr($$0,4); next} /^[a-zA-Z_-]+:.*## /{printf "  \033[36m%-18s\033[0m %s\n", substr($$1,1,length($$1)-1), substr($$0,index($$0,"## ")+3)}' $(MAKEFILE_LIST)
	@echo ""
