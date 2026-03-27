.PHONY: dev test build deploy clean help

dev:  ## Start development server
	cd backend && uvicorn app.main:app --reload --port 8000

dev-all:  ## Start all services (Docker)
	docker compose -f backend/docker-compose.yml up -d

test:  ## Run tests
	cd backend && python -m pytest tests/ -v --tb=short

lint:  ## Run linter
	cd backend && ruff check app/

build:  ## Build Docker image
	cd backend && docker build -t multisig-wallet .

migrate:  ## Run database migrations
	cd backend && alembic upgrade head

migrate-new:  ## Create new migration
	cd backend && alembic revision --autogenerate -m "$(msg)"

clean:  ## Stop Docker and clean cache
	docker compose -f backend/docker-compose.yml down -v
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
