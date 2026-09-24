from __future__ import annotations

import pytest

from odoo_instance_sdk.exceptions import (
    RemoteDatabaseAmbiguousError,
    RemoteDatabaseListUnavailableError,
    RemoteDatabaseNoneError,
)
from odoo_instance_sdk.internal.dbprep.source import (
    DatabaseNameProvider,
    resolve_remote_database_name,
)


class _FakeNamesProvider:
    def __init__(self, names: tuple[str, ...] | Exception) -> None:
        self._names = names

    def names(self) -> tuple[str, ...]:
        if isinstance(self._names, Exception):
            raise self._names
        return self._names


@pytest.mark.parametrize(
    ("configured", "provider_names", "expected"),
    [
        ("explicit_db", ("a", "b"), "explicit_db"),
        ("explicit_db", (), "explicit_db"),
        ("explicit_db", RuntimeError("nope"), "explicit_db"),
    ],
    ids=["explicit-skips-list-many", "explicit-skips-list-zero", "explicit-skips-unavailable"],
)
def test_explicit_database_has_priority(
    configured: str,
    provider_names: tuple[str, ...] | Exception,
    expected: str,
) -> None:
    provider: DatabaseNameProvider = _FakeNamesProvider(provider_names)
    assert resolve_remote_database_name(configured, provider) == expected


@pytest.mark.parametrize(
    ("provider_names", "expected"),
    [
        (("only_db",), "only_db"),
    ],
    ids=["single-auto-selected"],
)
def test_single_database_auto_selected(
    provider_names: tuple[str, ...],
    expected: str,
) -> None:
    provider: DatabaseNameProvider = _FakeNamesProvider(provider_names)
    assert resolve_remote_database_name(None, provider) == expected


def test_zero_databases_raises_remote_database_none() -> None:
    provider: DatabaseNameProvider = _FakeNamesProvider(())
    with pytest.raises(RemoteDatabaseNoneError) as exc_info:
        resolve_remote_database_name(None, provider)
    assert exc_info.value.code == "remote_database_none"


def test_many_databases_raises_ambiguous_with_names() -> None:
    provider: DatabaseNameProvider = _FakeNamesProvider(("db_a", "db_b", "db_c"))
    with pytest.raises(RemoteDatabaseAmbiguousError) as exc_info:
        resolve_remote_database_name(None, provider)
    assert exc_info.value.code == "remote_database_ambiguous"
    available = exc_info.value.details.get("available_databases")
    assert available == ["db_a", "db_b", "db_c"]


def test_unavailable_list_raises_distinct_error() -> None:
    provider: DatabaseNameProvider = _FakeNamesProvider(RuntimeError("connection refused"))
    with pytest.raises(RemoteDatabaseListUnavailableError) as exc_info:
        resolve_remote_database_name(None, provider)
    assert exc_info.value.code == "remote_database_list_unavailable"
