from __future__ import annotations

import math

import msgspec


class OdooModelInfo(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    name: str
    exists: bool
    description: str | None
    table: str | None
    abstract: bool | None
    transient: bool | None
    auto: bool | None
    field_count: int | None

    def __post_init__(self) -> None:
        _validate_identity(self.name, self.exists, "model")
        _validate_metadata(
            self.exists,
            (self.description, self.table),
            (self.abstract, self.transient, self.auto),
            (self.field_count,),
        )


class OdooFieldInfo(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    model: str
    name: str
    exists: bool
    label: str | None
    type: str | None
    required: bool | None
    readonly: bool | None
    store: bool | None
    index: bool | None
    relation: str | None
    related: bool | None
    computed: bool | None

    def __post_init__(self) -> None:
        _validate_identity(self.model, True, "model")
        _validate_identity(self.name, True, "field")
        _validate_metadata(
            self.exists,
            (self.label, self.type, self.relation),
            (self.required, self.readonly, self.store, self.index, self.related, self.computed),
            (),
        )


class OdooRefInfo(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    xmlid: str
    exists: bool
    model: str | None
    record_id: int | None
    display_name: str | None

    def __post_init__(self) -> None:
        if not self.xmlid or len(self.xmlid) > 256:
            raise ValueError("xmlid must be a non-empty bounded string")
        if type(self.exists) is not bool:
            raise TypeError("exists must be a boolean")
        _validate_metadata(self.exists, (self.model, self.display_name), (), (self.record_id,))
        if self.exists and self.record_id is None:
            raise ValueError("existing reference must have a positive record_id")
        if self.record_id is not None and self.record_id < 1:
            raise ValueError("record_id must be positive")


class OdooTestProfileEntry(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    test_id: str
    duration: float
    query_count: int

    def __post_init__(self) -> None:
        if not self.test_id or not self.test_id.strip():
            raise ValueError("test_id must not be blank")
        if isinstance(self.duration, bool) or not isinstance(self.duration, (int, float)):
            raise TypeError("duration must be a finite non-negative number")
        if not math.isfinite(self.duration) or self.duration < 0:
            raise ValueError("duration must be a finite non-negative number")
        if type(self.query_count) is not int or self.query_count < 0:
            raise ValueError("query_count must be a non-negative integer")


class OdooTestProfile(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    total_duration: float
    total_query_count: int
    entries: tuple[OdooTestProfileEntry, ...]

    def __post_init__(self) -> None:
        if isinstance(self.total_duration, bool) or not isinstance(
            self.total_duration, (int, float)
        ):
            raise TypeError("total_duration must be a finite non-negative number")
        if not math.isfinite(self.total_duration) or self.total_duration < 0:
            raise ValueError("total_duration must be a finite non-negative number")
        if type(self.total_query_count) is not int or self.total_query_count < 0:
            raise ValueError("total_query_count must be a non-negative integer")
        if type(self.entries) is not tuple:
            raise TypeError("entries must be a tuple")
        if (
            tuple(sorted(self.entries, key=lambda entry: (-entry.duration, entry.test_id)))
            != self.entries
        ):
            raise ValueError("entries must be sorted by duration descending then test_id")
        if self.total_duration != sum(entry.duration for entry in self.entries):
            raise ValueError("total_duration must equal the sum of entry durations")
        if self.total_query_count != sum(entry.query_count for entry in self.entries):
            raise ValueError("total_query_count must equal the sum of entry query counts")


def _validate_identity(value: str, exists: bool, field: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError(f"{field} identity must be a non-empty bounded string")
    if type(exists) is not bool:
        raise TypeError("exists must be a boolean")


def _validate_metadata(
    exists: bool,
    strings: tuple[str | None, ...],
    booleans: tuple[bool | None, ...],
    integers: tuple[int | None, ...],
) -> None:
    if type(exists) is not bool:
        raise TypeError("exists must be a boolean")
    if any(
        value is not None and (not isinstance(value, str) or len(value) > 256) for value in strings
    ):
        raise ValueError("metadata strings must be bounded to 256 characters")
    if any(value is not None and type(value) is not bool for value in booleans):
        raise TypeError("metadata flags must be booleans")
    if any(value is not None and (type(value) is not int or value < 0) for value in integers):
        raise ValueError("metadata counts must be non-negative integers")
    if not exists and any(value is not None for value in (*strings, *booleans, *integers)):
        raise ValueError("missing objects must not contain discovered metadata")


__all__ = [
    "OdooFieldInfo",
    "OdooModelInfo",
    "OdooRefInfo",
    "OdooTestProfile",
    "OdooTestProfileEntry",
]
