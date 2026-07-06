.PHONY: generate generate-check test lint

# Regenerate the CRD manifests from the Pydantic spec models
# (resources/schemas.py) into the CRD chart.
generate:
	uv run python scripts/gen_crds.py

# Fail if the committed CRDs are out of sync with the spec models.
generate-check: generate
	git diff --exit-code -- chart/airflow-k8s-operator-crds/templates

test:
	uv run pytest tests/ --ignore=tests/operator_test.py \
		--ignore=tests/variable_test.py --ignore=tests/giveup_test.py

lint:
	uv run ruff check .
