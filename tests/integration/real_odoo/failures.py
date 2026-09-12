"""Failure-injection and recovery contracts for focused real-Odoo tests.

The helpers in this module model publication and cleanup at the test boundary.
They deliberately do not add a second process or container lifecycle.
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
class FailureOutcome:
    """Stable machine-facing result of one injected failure."""

    error_code: str
    exit_code: int
    publication: PublicationState
    primary_error: str
    cleanup_errors: tuple[str, ...] = ()
    retained_files: tuple[Path, ...] = ()

    def as_machine_output(self) -> dict[str, object]:
        return {
            "ok": False,
            "error": {"code": self.error_code, "message": self.primary_error},
            "exit_code": self.exit_code,
            "publication": {
                "database": self.publication.database,
                "filestore": self.publication.filestore,
                "catalog": self.publication.catalog,
            },
            "cleanup_errors": list(self.cleanup_errors),
            "retained_files": [str(path) for path in self.retained_files],
        }


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


def run_injected_failure(
    run_id: str,
    root: Path,
    *,
    error_code: str,
    publish: Iterable[Literal["database", "filestore", "catalog"]] = (),
    fail_at: FailureStage = "restore",
    cleanup: Iterable[Callable[[], None]] = (),
    retain: bool = False,
) -> FailureOutcome:
    """Run a small publication transaction and retain primary error semantics."""
    ledger = ResourceLedger(run_id)
    proxy = PublicationProxy(run_id, root, ledger)
    for kind in publish:
        proxy.publish(kind)
    for index, callback in enumerate(cleanup):
        ledger.record("cleanup", f"{run_id}-cleanup-{index}", callback)
    primary = InjectedFailure(fail_at)
    cleanup_errors: list[str] = []
    try:
        ledger.unwind(primary_failure=primary)
    except BaseException as error:
        messages = _exception_messages(error)
        primary_text = str(primary)
        cleanup_errors.extend(message for message in messages if message != primary_text)
    state = PublicationState(
        database=(root / f"{run_id}-database").exists(),
        filestore=(root / f"{run_id}-filestore").exists(),
        catalog=(root / f"{run_id}-catalog").exists(),
    )
    retained_files: tuple[Path, ...] = ()
    if retain:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        retained = root / "failure.json"
        retained.write_text(
            json.dumps(
                {
                    "error_code": error_code,
                    "exit_code": 130 if fail_at == "interrupt" else 1,
                    "publication": {
                        "database": state.database,
                        "filestore": state.filestore,
                        "catalog": state.catalog,
                    },
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        retained.chmod(0o600)
        retained_files = (retained,)
    return FailureOutcome(
        error_code=error_code,
        exit_code=130 if fail_at == "interrupt" else 1,
        publication=state,
        primary_error=str(primary),
        cleanup_errors=tuple(cleanup_errors),
        retained_files=retained_files,
    )


def _exception_messages(error: BaseException) -> tuple[str, ...]:
    if isinstance(error, BaseExceptionGroup):
        return tuple(
            message for child in error.exceptions for message in _exception_messages(child)
        )
    return (str(error),)


def write_archive_variant(path: Path, variant: Literal["truncated", "incompatible"]) -> None:
    """Write a malformed but deterministic archive for pre-publication checks."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if variant == "truncated":
        path.write_bytes(b"PK\x03\x04truncated")
        return
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("dump.sql", "-- database: incompatible\n")
        archive.writestr("filestore/incompatible/blob", b"fixture")
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
    "FailureOutcome",
    "InjectedFailure",
    "PublicationProxy",
    "PublicationState",
    "assert_secret_free",
    "run_injected_failure",
    "secret_variants",
    "write_archive_variant",
    "write_failure_evidence",
]
