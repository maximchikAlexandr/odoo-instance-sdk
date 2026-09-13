"""Small, shared helpers for observing real-Odoo failure boundaries.

These helpers deliberately do not manufacture a public result.  The focused
tests obtain the result from OdCLI, then use this module only to retain the
primary exception while the shared run ledger unwinds.
"""

from __future__ import annotations

import base64
import hashlib
import json
import urllib.parse
import zipfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal

from .cleanup import FailureEvidence, ResourceLedger

FailureStage = Literal[
    "authentication",
    "source",
    "archive",
    "restore",
    "database-publication",
    "filestore-publication",
    "interrupt",
    "timeout",
]

FOCUSED_EVIDENCE: Final[tuple[str, ...]] = tuple(f"E2E-FC-{number:02d}" for number in range(1, 14))
RECOVERY_EVIDENCE: Final[tuple[str, ...]] = tuple(f"E2E-REC-{number:02d}" for number in range(1, 4))
SECURITY_EVIDENCE: Final[tuple[str, ...]] = tuple(f"E2E-SEC-{number:02d}" for number in range(1, 4))


@dataclass(frozen=True, slots=True)
class PublicationState:
    """Observable publication state after a failed restore attempt."""

    database: bool = False
    filestore: bool = False
    catalog: bool = False

    @property
    def published(self) -> bool:
        return self.database or self.filestore or self.catalog


@dataclass(frozen=True, slots=True)
class RecoveryObservation:
    """Observed exception and cleanup state from one real boundary call."""

    primary_error: BaseException
    cleanup_errors: tuple[str, ...] = ()

    @property
    def exit_code(self) -> int:
        return 130 if isinstance(self.primary_error, KeyboardInterrupt) else 1


class InjectedFailure(RuntimeError):
    """A deterministic boundary failure used only by focused tests."""

    def __init__(self, stage: FailureStage, message: str | None = None) -> None:
        self.stage = stage
        super().__init__(message or f"injected failure at {stage}")


@dataclass(slots=True)
class PublicationProxy:
    """Create run-owned publication markers and compensate them on failure."""

    run_id: str
    root: Path
    ledger: ResourceLedger
    state: PublicationState = field(default_factory=PublicationState)

    def __post_init__(self) -> None:
        if not self.run_id or self.run_id not in self.root.name:
            raise ValueError("publication root must be namespaced to run_id")

    def publish(self, kind: Literal["database", "filestore", "catalog"]) -> Path:
        path = self.root / f"{self.run_id}-{kind}"
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text(kind + "\n", encoding="utf-8")
        path.chmod(0o600)

        def cleanup() -> None:
            path.unlink(missing_ok=True)

        self.ledger.record(kind, path.name, cleanup)
        self.state = PublicationState(
            database=self.state.database or kind == "database",
            filestore=self.state.filestore or kind == "filestore",
            catalog=self.state.catalog or kind == "catalog",
        )
        return path


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


def secret_variants(secret: str) -> tuple[str, ...]:
    """Return common reversible/one-way encodings to scan from evidence."""
    return (
        secret,
        hashlib.sha256(secret.encode()).hexdigest(),
        base64.b64encode(secret.encode()).decode(),
        urllib.parse.quote(secret, safe=""),
    )


def assert_secret_free(values: Mapping[str, object] | Iterable[Path], secret: str) -> None:
    """Fail closed if raw or common encoded secret values reach evidence."""
    if isinstance(values, Mapping):
        haystack = json.dumps(values, default=str, sort_keys=True)
    else:
        haystack = "\n".join(path.read_text(encoding="utf-8") for path in values)
    leaked = [variant for variant in secret_variants(secret) if variant and variant in haystack]
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


__all__ = [
    "FOCUSED_EVIDENCE",
    "RECOVERY_EVIDENCE",
    "SECURITY_EVIDENCE",
    "InjectedFailure",
    "PublicationProxy",
    "PublicationState",
    "RecoveryObservation",
    "assert_secret_free",
    "run_recovery_action",
    "secret_variants",
    "write_archive_variant",
    "write_failure_evidence",
]
