.PHONY: install lint typecheck test format ingest features ratings train predict backtest clean

UV ?= uv
PYTHON ?= $(UV) run python

install:
	$(UV) sync --group dev

lint:
	$(UV) run ruff check src tests
	$(UV) run ruff format --check src tests

typecheck:
	$(UV) run mypy

test:
	$(UV) run pytest

format:
	$(UV) run ruff check --fix src tests
	$(UV) run ruff format src tests

ingest:
	$(PYTHON) -m ncaa_quant.cli ingest

features:
	$(PYTHON) -m ncaa_quant.cli features

ratings:
	$(PYTHON) -m ncaa_quant.cli ratings

train:
	$(PYTHON) -m ncaa_quant.cli train

predict:
	$(PYTHON) -m ncaa_quant.cli predict

backtest:
	$(PYTHON) -m ncaa_quant.cli backtest

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage coverage.xml
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
