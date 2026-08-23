from __future__ import annotations

from pathlib import Path

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    root = Path(__file__).resolve().parent
    for item in items:
        rel = Path(item.path).relative_to(root).as_posix()
        if rel.startswith("packaging/"):
            item.add_marker(pytest.mark.packaging)
        elif rel.startswith("integration/"):
            item.add_marker(pytest.mark.integration)
        else:
            item.add_marker(pytest.mark.unit)
