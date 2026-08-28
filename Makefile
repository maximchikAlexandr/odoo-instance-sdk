.DEFAULT_GOAL := help

.PHONY: help lint types test targeted coverage mutation package compat dashboard pgadmin-smoke smoke live web-codegen web-codegen-check pr

OFFLINE := not real_odoo and not packaging and not dashboard

help:
	@printf '%s\n' 'make lint|types|test|targeted|coverage|mutation|package|compat|dashboard|pgadmin-smoke|smoke|live|web-codegen|web-codegen-check|pr'

lint:
	uv run ruff format --check .
	uv run ruff check .

types:
	uv run mypy --strict src/odoo_instance_sdk
	uv run mypy tests scripts --namespace-packages --explicit-package-bases --ignore-missing-imports --follow-imports=silent --check-untyped-defs

test:
	uv run pytest -o addopts="" -v --tb=short --strict-markers \
		-m "$(OFFLINE) and not serial" -n auto --dist loadscope \
		--cov=odoo_instance_sdk --cov-branch --cov-report=
	uv run pytest -o addopts="" -v --tb=short --strict-markers \
		-m "serial and $(OFFLINE)" --cov=odoo_instance_sdk --cov-branch --cov-append \
		--cov-report=term-missing --cov-report=xml --cov-report=json
	uv run python scripts/check_coverage.py --coverage-json coverage.json

targeted:
	uv run pytest -q $(PYTEST_ARGS)

coverage: test

mutation:
	mkdir -p .artifacts/mutation
	NO_COLOR=1 uv run mutmut run
	NO_COLOR=1 uv run mutmut results | tee .artifacts/mutation/results.txt

package:
	rm -rf dist
	cd src/odoo_instance_sdk/web && npm ci && npm run build
	uv build
	@test "$$(find dist -maxdepth 1 -name '*.whl' | wc -l | tr -d ' ')" -eq 1
	@test "$$(find dist -maxdepth 1 -name '*.tar.gz' | wc -l | tr -d ' ')" -eq 1
	uv run pytest tests/packaging/ -v -o addopts="" -m packaging

compat:
	uv run pytest -o addopts="" -q --tb=short --strict-markers \
		-m "$(OFFLINE) and not serial" -n auto --dist loadscope
	uv run pytest -o addopts="" -q --tb=short --strict-markers -m "serial and $(OFFLINE)"

dashboard:
	cd src/odoo_instance_sdk/web && npm ci
	make web-codegen-check
	cd src/odoo_instance_sdk/web && npm test && npm run build
	uv run pytest -q -o addopts='' -m dashboard

pgadmin-smoke:
	uv run pytest -q tests/integration/test_pgadmin_docker_smoke.py -o addopts='' -m integration

web-codegen:
	uv run python scripts/export_openapi.py
	cd src/odoo_instance_sdk/web && npm run generate

web-codegen-check:
	uv run python scripts/check_web_codegen.py

smoke:
	uv run pytest -q tests/integration/test_monitor_smoke.py -o addopts='' -m integration

live:
	@if [ "$$ODCLI_REAL_ODOO_ENABLE" != "1" ]; then \
		printf '%s\n' 'make live requires ODCLI_REAL_ODOO_ENABLE=1 and ODCLI_REAL_PROJECT, ODCLI_REAL_ODOO_BIN, ODCLI_REAL_PYTHON, ODCLI_REAL_CONFIG, ODCLI_REAL_DATABASE'; \
		exit 1; \
	fi
	uv run pytest -o addopts="" -v --tb=short --strict-markers -m real_odoo

pr: lint types test compat dashboard smoke package
