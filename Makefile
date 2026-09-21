.DEFAULT_GOAL := help

UV ?= uv
PYTEST_ARGS ?=

.PHONY: help setup lint test test-slow typecheck check companion-contracts browser-test

help: ## List available tasks
	@awk 'BEGIN {FS = ":.*## "; printf "Usage: make <cible>\n\nCibles disponibles:\n"} /^[a-zA-Z0-9_.-]+:.*## / {printf "  %-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: ## Install development dependencies
	$(UV) sync --group dev

lint: ## Run Ruff checks
	$(UV) run ruff check src tests

test: ## Run the default test suite
	$(UV) run pytest $(PYTEST_ARGS)

test-slow: ## Run tests marked as slow
	$(UV) run pytest -m slow $(PYTEST_ARGS)

typecheck: ## Run mypy type checks
	$(UV) run mypy

companion-contracts: ## Validate contracts with companion repositories
	$(UV) run python scripts/check_companion_contracts.py

browser-test: ## Run browser export tests
	$(UV) run pytest -m slow tests/test_browser_export.py $(PYTEST_ARGS)

check: lint typecheck test ## Run the main checks
