# Personal Finance Hub — common tasks. Run `make` to see them.
.DEFAULT_GOAL := help

VENV := .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip

.PHONY: help setup run dev seed reset-data clean

help: ## Show this help
	@echo "Personal Finance Hub"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-11s\033[0m %s\n", $$1, $$2}'

setup: ## One-time: create venv, install deps, create .env
	python3 -m venv $(VENV)
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -r requirements.txt
	@test -f .env || cp .env.example .env
	@echo ""
	@echo "✅ Setup complete."
	@echo "   (optional) edit .env to add an LLM key for the advisor, then:"
	@echo "   make run"

run: ## Start the app at http://127.0.0.1:8888
	$(PY) -m app.api --host 127.0.0.1 --port 8888

dev: ## Start with auto-reload (for development)
	$(PY) -m uvicorn app.api:app --host 127.0.0.1 --port 8888 --reload

seed: ## Fill the DB with ~14 months of realistic fake data to explore
	$(PY) scripts/seed_fake_data.py --reset --months 14

reset-data: ## Delete your local database and start fresh
	rm -f data/finance.db
	@echo "🗑  Local data cleared."

clean: ## Remove the venv and caches (your data is kept)
	rm -rf $(VENV) .pytest_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@echo "🧹 Cleaned. Run 'make setup' to rebuild."
