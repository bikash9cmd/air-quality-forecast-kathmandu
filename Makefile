PY ?= python

.PHONY: setup data quality backtest train replay serve test lint docker-up validate-infra app

setup:            ## create a virtualenv with dev dependencies
	$(PY) -m venv .venv && .venv/bin/pip install -r requirements-dev.txt

data:             ## download raw files, verify checksums
	$(PY) -m hawa download

quality: data     ## data quality report
	$(PY) -m hawa quality

backtest: data    ## walk-forward backtest -> artifacts/reports (about 5 minutes)
	$(PY) -m hawa backtest

train: data       ## train with promotion gate
	$(PY) -m hawa train --until 2017-12-01

replay: train     ## run the hourly job over the first week of December 2017
	$(PY) -m hawa replay --start 2017-12-01T00:00 --end 2017-12-07T23:00

serve:            ## HTTP API on :8000
	$(PY) -m hawa serve

test:
	$(PY) -m pytest -q

lint:
	ruff check .

validate-infra:   ## lint the CloudFormation/SAM template
	cfn-lint infra/template.yaml

docker-up:
	docker compose run --rm pipeline && docker compose up api

app:              ## the Streamlit demo on http://localhost:8501 (pip install -r requirements-app.txt first)
	$(PY) -m streamlit run app.py
