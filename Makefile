UV ?= uv
NPM ?= npm
export UV_CACHE_DIR ?= $(CURDIR)/.uv-cache

.PHONY: install lint test coverage security dependency-check pr-check build check

install:
	$(UV) sync --frozen --extra dev
	cd frontend && $(NPM) ci

lint:
	$(UV) run --frozen --extra dev ruff check backend
	$(UV) run --frozen --extra dev ruff format --check backend
	cd frontend && $(NPM) run lint
	cd frontend && $(NPM) run typecheck

test:
	$(UV) run --frozen --extra dev python -m pytest

coverage:
	$(UV) run --frozen --extra dev python -m pytest --cov=backend.app --cov-branch --cov-report=term-missing --cov-fail-under=80

security:
	$(UV) run --frozen --extra dev bandit --severity-level medium --confidence-level medium --recursive backend --exclude backend/tests

dependency-check:
	$(UV) lock --check
	$(UV) run --frozen --extra dev pip-audit
	cd frontend && $(NPM) audit --audit-level=high

pr-check:
	git diff --check
	$(UV) run --frozen --extra dev python -m compileall -q backend

build:
	docker build --target web -t swag-ui:local .
	docker build --target helper -t swag-ui-helper:local .

check: lint test security pr-check
