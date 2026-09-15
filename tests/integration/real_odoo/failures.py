"""Small, shared helpers for observing real-Odoo failure boundaries.

These helpers deliberately do not manufacture a public result.  The focused
tests obtain the result from OdCLI, then use this module only to retain the
primary exception while the shared run ledger unwinds.
"""

from __future__ import annotations

import json
import zipfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from scripts.real_odoo_secrets import secret_variants

from .cleanup import FailureEvidence, ResourceLedger


@dataclass(frozen=True, slots=True)
class RecoveryObservation:
    """Observed exception and cleanup state from one real boundary call."""

    primary_error: BaseException
    cleanup_errors: tuple[str, ...] = ()


def run_recovery_action(
    action: Callable[[], object],
    *,
    ledger: ResourceLedger,
) -> RecoveryObservation:
    """Run a real action and unwind the caller's shared ledger.

    ``action`` is intentionally supplied by the test.  In particular, this
    function never chooses an error code, exit status, or publication state;
    those values must come from the public CLI/SDK boundary under test.
    """
    try:
        action()
    except BaseException as primary:
        cleanup_errors: list[str] = []
        try:
            ledger.unwind(primary_failure=primary)
        except BaseException as error:
            for message in _exception_messages(error):
                if message != str(primary):
                    cleanup_errors.append(message)
        return RecoveryObservation(primary, tuple(cleanup_errors))
    ledger.unwind()
    raise AssertionError("recovery action unexpectedly succeeded")


def _exception_messages(error: BaseException) -> tuple[str, ...]:
    if isinstance(error, BaseExceptionGroup):
        return tuple(
            message for child in error.exceptions for message in _exception_messages(child)
        )
    return (str(error),)


def write_archive_variant(path: Path, variant: Literal["truncated", "incompatible"]) -> None:
    """Write a deterministic archive with either structural or identity failure."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if variant == "truncated":
        path.write_bytes(b"PK\x03\x04truncated")
        return
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", '{"db_name": "not-the-catalogue-database"}')
        archive.writestr(
            "dump.sql",
            "-- database: not-the-catalogue-database\n\\connect not-the-catalogue-database\n",
        )
        archive.writestr("filestore/not-the-catalogue-database/blob", b"fixture")
    path.chmod(0o600)


def assert_secret_free(values: Mapping[str, object] | Iterable[Path], secret: str) -> None:
    """Fail closed if raw or common encoded secret values reach evidence."""
    variants = tuple(variant for variant in secret_variants(secret) if variant)
    if isinstance(values, Mapping):
        text_haystack = json.dumps(values, default=str, sort_keys=True)
        leaked = [variant for variant in variants if variant in text_haystack]
    else:
        byte_haystack = b"\n".join(path.read_bytes() for path in values)
        leaked = [variant for variant in variants if variant.encode("utf-8") in byte_haystack]
    assert not leaked, f"secret material leaked into focused evidence: {leaked!r}"


def write_failure_evidence(
    evidence: FailureEvidence,
    *,
    logs: Mapping[str, str],
) -> tuple[Path, ...]:
    """Add bounded logs and write the shared evidence bundle."""
    for name, text in logs.items():
        evidence.add_log(name, text)
    return evidence.write()
