## Why

`env checkout` сейчас компилирует `requirements.lock` без выбранного Python, хотя следующий install/sync запускается для записанного interpreter. Из-за этого `uv` может закрепить версии, несовместимые с целевым Python, и валидный checkout завершается ошибкой уже после создания артефактов.

## What Changes

- Передавать в checkout-команду `uv pip compile` тот же разрешённый interpreter, который checkout использует для последующего `uv pip install` или `uv pip sync`.
- Сохранять существующие различия между reused и owned Python environments, порядок immutable checkout plan и hash-lock bypass.
- Закрепить регрессионным unit test совпадение Python target в compile и install/sync шагах checkout.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

- `development-environment`: dependency lock, создаваемый во время checkout, должен разрешаться для того же Python interpreter, в который затем устанавливаются зависимости.

## Impact

- Затронуты построение immutable checkout steps в `src/odoo_instance_sdk/resources/environment/checkout_artifacts.py` и focused coverage в `tests/unit/resources/test_environment_python.py`.
- Публичные SDK/CLI signatures, catalog schema, dependency set и обычный `env sync` не меняются.
- Hash-lock checkout не компилирует зависимости и остаётся вне изменяемой ветки.
