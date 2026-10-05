.DEFAULT_GOAL := help
TEST_DB ?= postgresql://postgres:postgres@localhost:5432/support_agent

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Install deps + git hooks
	uv sync
	uv run pre-commit install

lint: ## Lint + format check
	uv run ruff check .
	uv run ruff format --check .

fmt: ## Auto-format
	uv run ruff format .
	uv run ruff check --fix .

typecheck: ## mypy --strict
	uv run mypy

test: ## Fast tests (no external services)
	uv run pytest

test-db: ## Tests incl. real Postgres/pgvector (needs `make up`)
	TEST_DATABASE_URL=$(TEST_DB) uv run pytest

check: lint typecheck test ## Everything CI runs

run: ## Run API locally with hot reload
	uv run uvicorn --factory support_agent.main:create_app --reload --port 8000

up: ## Start local stack (API + Postgres)
	docker compose up -d --build

down: ## Stop local stack
	docker compose down

ingest: ## Load knowledge base into pgvector
	uv run python -m support_agent.rag.ingest --path data/knowledge_base

evals: ## Run evals against REAL Azure OpenAI (costs tokens)
	uv run python -m evals.run_evals

graph: ## Re-render the agent graph diagram (docs/graph.mmd)
	uv run python -m scripts.render_graph > docs/graph.mmd
