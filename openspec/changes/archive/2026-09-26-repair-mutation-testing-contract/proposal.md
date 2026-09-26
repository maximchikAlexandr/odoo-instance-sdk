## Why

Плановый workflow `Mutation Testing` шесть раз показывал зелёный итог, хотя `mutmut` падал при сборе baseline-тестов до проверки первого мутанта. Текущий targeted scope копируется в неполное дерево `mutants/`, где тесты не могут импортировать `odoo_instance_sdk.cli`; job-level `continue-on-error` затем маскирует отказ, а `make mutation` не оставляет отчёт об ошибке.

Нужен честный и воспроизводимый диагностический gate: полный пакет должен быть доступен тестам, мутации должны оставаться ограниченными пятью security/normalization-модулями, а любой bootstrap/collection/execution failure должен быть виден как красный workflow с диагностическим artifact.

## What Changes

- Исправить конфигурацию `mutmut` 3.x так, чтобы baseline и mutant tests импортировали полный `odoo_instance_sdk`, но mutation targets оставались ровно пятью уже согласованными файлами.
- Усилить `make mutation`: всегда создавать непустой `.artifacts/mutation/results.txt`, сохранять итог категорий `killed`/`survived`/`timeout`/`suspicious` при успешном запуске и записывать диагностику при отказе до отчёта, не скрывая исходный non-zero exit.
- Убрать job-level `continue-on-error` из scheduled/manual workflow; оставить workflow диагностическим и non-required только через repository branch-protection policy, а не через преобразование failure в success.
- Добавить узкий regression harness, который доказывает фактический запуск мутанта при тестовом импорте `odoo_instance_sdk.cli`, сохранение targeted scope и fail-closed поведение при контролируемой collection error.
- Синхронизировать developer-документацию с единым контрактом `make mutation` для локального, scheduled и manual запуска.

## Capabilities

### New Capabilities

- `mutation-testing`: воспроизводимый targeted mutation audit, его локальный Make-контракт, CI-семантика, диагностический artifact и regression proof.

### Modified Capabilities

- None.

## Impact

- Конфигурация и зависимости: `pyproject.toml`, при необходимости `uv.lock` только для поддерживаемой конфигурации существующего `mutmut>=3,<4` без нового mutation framework.
- Developer/CI entrypoints: `Makefile`, `.github/workflows/mutation.yml`.
- Verification: узкие tests/fixtures/scripts вокруг mutation contract; существующие unit tests целевых модулей переиспользуются.
- Documentation: `CONTRIBUTING.md` и, только если нужен публичный development summary, `README.md`.
- Public Python API, production `odcli`, production runtime, PR-required checks и широкий mutation scope не изменяются.
