.PHONY: help install test lint typecheck format run docker-up docker-down docker-logs eval-generate eval-chargeback eval-fraud eval-gates clean

help:
	@echo "VAJRA - Multi-Agent Defense System for Payment Risk"
	@echo ""
	@echo "Available commands:"
	@echo "  install          - Install dependencies"
	@echo "  test             - Run all tests"
	@echo "  test-unit        - Run unit tests only"
	@echo "  test-integration - Run integration tests only"
	@echo "  lint             - Run ruff linter"
	@echo "  typecheck        - Run mypy type checker"
	@echo "  format           - Format code with ruff"
	@echo "  run              - Run API server locally"
	@echo "  docker-up        - Start Docker services"
	@echo "  docker-down      - Stop Docker services"
	@echo "  docker-logs      - View Docker logs"
	@echo "  eval-generate    - Generate synthetic eval datasets"
	@echo "  eval-chargeback  - Run chargeback defender evaluation"
	@echo "  eval-fraud       - Run fraud vajra evaluation"
	@echo "  eval-gates       - Check CI eval gates"
	@echo "  clean            - Clean build artifacts"

install:
	pip install -e ".[dev]"

test:
	pytest tests/ -v --tb=short

test-unit:
	pytest tests/ -v --tb=short -m "not integration"

test-integration:
	pytest tests/integration -v --tb=short

lint:
	ruff check vajra tests

typecheck:
	mypy vajra

format:
	ruff format vajra tests

run:
	python -m vajra

docker-up:
	docker-compose up -d postgres redis clickhouse kafka zookeeper

docker-down:
	docker-compose down -v

docker-logs:
	docker-compose logs -f

eval-generate:
	python -m vajra.eval.generate_datasets

eval-chargeback:
	python -m vajra.eval.run_chargeback_eval

eval-fraud:
	python -m vajra.eval.run_fraud_eval

eval-gates:
	python -m vajra.eval.check_gates

clean:
	rm -rf build dist *.egg-info .pytest_cache .mypy_cache .ruff_cache __pycache__ vajra/__pycache__ vajra/**/__pycache__
	rm -rf /tmp/vajra_* /tmp/mlruns

db-init:
	python -c "import asyncio; from vajra.core.database import init_db; asyncio.run(init_db())"

db-migrate:
	alembic upgrade head

db-revision:
	alembic revision --autogenerate -m "$(msg)"