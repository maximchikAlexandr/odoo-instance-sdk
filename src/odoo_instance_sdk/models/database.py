"""Typed database observations and explicit reconciliation results."""

from __future__ import annotations

from typing import Literal

import msgspec

DatabaseEvidenceSource = Literal["odoo", "psql"]


class DatabaseObservation(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """One read-only view of live and catalogue database identities.

    ``missing_names`` is deliberately limited to names tracked by the local
    catalogue.  A live probe that cannot prove its result is represented by
    ``inconclusive`` and cannot be passed to reconciliation.
    """

    names: tuple[str, ...]
    evidence_source: DatabaseEvidenceSource
    tracked_names: tuple[str, ...] = ()
    missing_names: tuple[str, ...] = ()
    inconclusive: bool = False
    cluster_host: str | None = None
    cluster_port: int | None = None


class DatabaseReconciliationResult(
    msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True
):
    """The idempotent audit changes made by explicit reconciliation."""

    cluster_host: str
    cluster_port: int
    reconciled_names: tuple[str, ...]


__all__ = [
    "DatabaseEvidenceSource",
    "DatabaseObservation",
    "DatabaseReconciliationResult",
]
