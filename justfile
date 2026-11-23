set shell := ["bash", "-cu"]

check:
	uv lock --check
	uv sync --locked
	uv run --locked python scripts/build_runtime_bundles.py --check
	uv run --locked python scripts/generate_adapters.py --check
	uv run --locked python scripts/validate_test_split.py
	uv run --locked ruff format --check .
	uv run --locked ruff check .
	uv run --locked mypy .
	uv run --locked pytest -m "not integration" -n logical

integration:
	uv run --locked python scripts/validate_test_split.py
	uv run --locked python scripts/run_integration.py

test-split:
	uv run --locked python scripts/validate_test_split.py

typecheck:
	uv run --locked mypy .

test:
	uv run --locked pytest

format:
	uv run --locked ruff format .
	uv run --locked ruff check --fix .

scaffold-smoke:
	uv run --locked python -c "from astack.scaffold import scaffold_smoke_test; print(scaffold_smoke_test())"

validation-benchmark:
	uv run --locked python scripts/validation_session.py --benchmark-mode serial
	uv run --locked python scripts/validation_session.py --benchmark-mode parallel

ship-ready:
	uv run --locked python scripts/validation_session.py --name maintainer

new target name package description intended_use:
	uv run --locked astack new "{{target}}" --name "{{name}}" --package "{{package}}" --description "{{description}}" --intended-use "{{intended_use}}"

upgrade target:
	uv run --locked astack install "{{target}}" --candidate
