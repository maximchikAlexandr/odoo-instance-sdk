"""Small, test-only contracts shared by real-Odoo verification tests."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any, Literal

E2EDisposition = Literal["critical", "focused", "smoke", "not-applicable"]
E2E_DISPOSITIONS = frozenset({"critical", "focused", "smoke", "not-applicable"})
_EVIDENCE_ID = re.compile(r"E2E-(?:SM|CP|FC|REC|SEC)-\d{2}\Z")


class ContractError(ValueError):
    """Raised when a test contract is incomplete or inconsistent."""


def validate_leaf_metadata(
    cases: Iterable[Any],
    *,
    registered_paths: set[tuple[str, ...]] | None = None,
) -> tuple[Any, ...]:
    """Validate complete, one-row-per-leaf E2E metadata at collection time."""
    rows = tuple(cases)
    paths = tuple(case.path for case in rows)
    if len(paths) != len(set(paths)):
        raise ContractError("PUBLIC_LEAF_CASES contains duplicate paths")
    if registered_paths is not None and set(paths) != registered_paths:
        missing = sorted(registered_paths - set(paths))
        extra = sorted(set(paths) - registered_paths)
        raise ContractError(f"leaf inventory drift: missing={missing!r}, extra={extra!r}")

    for case in rows:
        if case.e2e_disposition not in E2E_DISPOSITIONS:
            raise ContractError(f"missing E2E disposition for {' '.join(case.path)}")
        if not case.e2e_evidence and case.e2e_disposition != "not-applicable":
            raise ContractError(f"missing E2E evidence for {' '.join(case.path)}")
        if any(not _EVIDENCE_ID.fullmatch(identifier) for identifier in case.e2e_evidence):
            raise ContractError(f"invalid E2E evidence for {' '.join(case.path)}")
        if not case.e2e_rationale.strip():
            raise ContractError(f"missing E2E rationale for {' '.join(case.path)}")
    return rows


def matrix_row(case: Any) -> str:
    """Render one deterministic row in canonical inventory order."""
    path = " ".join(case.path)
    dry_run = "yes" if "--dry-run" in case.args or case.requires_dry_run else "no"
    evidence = " / ".join(case.e2e_evidence)
    evidence_column = f"{evidence}: " if evidence else ""
    return (
        f"| `{path}` | {case.classification} | {dry_run} | "
        f"{case.e2e_disposition} | {evidence_column}{case.e2e_rationale} |"
    )


_MATRIX_PREFIX = "# Public CLI traceability matrix\n\n"
CANONICAL_INVENTORY_BASE = "af9e1b3e8d127145b9488f11ec79519f9442db46"
ORIGINAL_AUDIT_BASE = "0ff164636617c03a51277055af45cef009277368"
CANONICAL_LEAF_COUNT = 50
_MATRIX_PROVENANCE = (
    "This is a reviewed projection of "
    "`tests/unit/test_cli_output_modes.py::PUBLIC_LEAF_CASES` at "
    f"canonical-inventory base `{CANONICAL_INVENTORY_BASE}`; the original "
    f"full-change audit base remains `{ORIGINAL_AUDIT_BASE}`. It is not a source "
    "registry. Implementation adds the disposition and evidence fields to each "
    "existing `PublicLeafCase`; the generator SHALL emit this exact provenance, "
    "rewrite the complete 50-row table, and fail the check on any byte drift. "
    "`smoke` means covered in PR smoke and full; `critical` means the full "
    "critical path; `focused` means a full-tier case around the critical path; "
    "`not-applicable` requires the recorded reason."
)
_MATRIX_HEADER = (
    "| Public leaf | Existing class | Dry-run | E2E disposition | Evidence / rationale |\n"
    "| --- | --- | ---: | --- | --- |"
)


def render_matrix_table(cases: Sequence[Any]) -> str:
    """Return the reviewed projection table without a second leaf registry."""
    validate_leaf_metadata(cases)
    return "\n".join((_MATRIX_HEADER, *(matrix_row(case) for case in cases)))


def render_matrix_document(existing: str, cases: Sequence[Any]) -> str:
    """Replace only the generated table, preserving reviewed scenario prose."""
    if len(cases) != CANONICAL_LEAF_COUNT:
        raise ContractError(f"expected {CANONICAL_LEAF_COUNT} canonical leaves")
    marker = "\n## Scenario coverage\n"
    if marker not in existing:
        raise ContractError("command matrix is missing its scenario coverage section")
    tail = existing[existing.index(marker) :]
    return _MATRIX_PREFIX + _MATRIX_PROVENANCE + "\n\n" + render_matrix_table(cases) + "\n" + tail


def check_matrix_document(path: str, cases: Sequence[Any]) -> None:
    """Validate the complete byte-equal generated matrix."""
    from pathlib import Path

    matrix_path = Path(path)
    actual = matrix_path.read_text(encoding="utf-8")
    expected = render_matrix_document(actual, cases)
    if actual != expected:
        raise ContractError(f"stale generated command matrix: {matrix_path}")
