# ADR-0001: Version независима от Odoo

## Дата
2026-07-15

## Статус
Accepted

## Контекст
Изначально версия пакета была `19.0.0b1` — привязана к версии Odoo (19.0), которую
SDK поддерживает. Это создаёт ложное впечатление зрелого продукта: пользователь
видит `19.0.0` и ожидает feature-complete стабильный релиз, хотя SDK находится на
ранней стадии (`0.1.0`).

Версия Odoo — это **compatibility target**, указан в `description` и `requires-python`.
Версия пакета — это **maturity и API evolution** SDK. Это два независимых числа.

## Решение
Версия пакета начинается с `0.1.0` и следует SemVer. Версия Odoo не входит в
номер пакета.

- `version = "0.1.0"` в `pyproject.toml`, `__init__.py`
- `description` указывает совместимость: "for managing local Odoo 19.0 instances"
- `classifiers` могут включать `Framework :: Odoo` если есть такой classifier
- CHANGELOG ведётся по SemVer, начиная с `0.1.0`

## Последствия
- Breaking changes в `0.x` не требуют major bump (SemVer 0.x convention)
- Пользователь видит реальную зрелость продукта
- При достижении стабильности → `1.0.0`