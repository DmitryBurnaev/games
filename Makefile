.DEFAULT_GOAL := help
UV ?= uv

.PHONY: help
help: ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*? / {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

.PHONY: install
install: ## Install local dependencies
	$(UV) sync

.PHONY: check
check: ## Run Django system checks
	$(UV) run python src/manage.py check

.PHONY: test
test: ## Run Django tests
	$(UV) run python src/manage.py test backgammon games

.PHONY: format
format: ## Format Python code
	$(UV) run black .

.PHONY: lint
lint: ## Check formatting, Python lint, JavaScript syntax and Django settings
	$(UV) run black --check .
	$(UV) run ruff check src
	node --check src/backgammon/static/backgammon/game.js
	$(UV) run python src/manage.py check

.PHONY: typecheck
typecheck: ## Run the experimental Django-aware Python type check
	PYTHONPATH=src $(UV) run mypy src

.PHONY: migrate
migrate: ## Apply local migrations
	$(UV) run python src/manage.py migrate

.PHONY: run
run: ## Run the local Django development server
	$(UV) run python src/manage.py runserver

.PHONY: run-in-docker
run-in-docker: .env ## Run app through Docker Compose
	docker compose up app --build

.PHONY: test-in-docker
test-in-docker: .env ## Run tests through Docker Compose
	docker compose up test --build --exit-code-from test

.PHONY: lint-in-docker
lint-in-docker: .env ## Run lint checks through Docker Compose
	docker compose up lint --build --exit-code-from lint

.PHONY: env
env: .env ## Create .env from template if missing

.env:
	@test -f .env || cp .env.template .env
