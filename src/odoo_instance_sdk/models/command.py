from __future__ import annotations

from typing import Literal

import msgspec

from odoo_instance_sdk.models.backup import Backup


class CommandResult(msgspec.Struct):
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration: float
    cwd: str | None = None
    environment: tuple[tuple[str, str], ...] = ()
    timeout: float | None = None

    @property
    def argv(self) -> list[str]:
        """Canonical spelling for the exact argument vector."""

        return self.args

    @property
    def env(self) -> tuple[tuple[str, str], ...]:
        """Compatibility spelling for the captured child environment."""

        return self.environment


class OdooTestSpec(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """The transport-neutral input contract for one native Odoo test run."""

    modules: tuple[str, ...]
    test_tags: str
    reload_tests: bool = False
    allow_empty: bool = False

    def __post_init__(self) -> None:
        if not self.modules:
            raise ValueError("OdooTestSpec.modules must not be empty")
        if tuple(sorted(set(self.modules))) != self.modules:
            raise ValueError("OdooTestSpec.modules must be sorted and unique")
        if not isinstance(self.test_tags, str) or not self.test_tags.strip():
            raise ValueError("OdooTestSpec.test_tags must not be blank")


class OdooTestResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """The stable native report/result contract for one Odoo test run."""

    counts: dict[str, int]
    failures: bool
    zero_tests: bool
    exit_code: int

    def __post_init__(self) -> None:
        required = {"tests", "successful", "failed", "errors", "skipped"}
        if set(self.counts) != required:
            raise ValueError("OdooTestResult.counts must contain exactly five count keys")
        if any(type(value) is not int or value < 0 for value in self.counts.values()):
            raise ValueError("OdooTestResult.counts values must be non-negative integers")


class OdooProcess(msgspec.Struct):
    id: str
    pid: int
    args: list[str]
    started_at: float

    def __repr__(self) -> str:
        masked: list[str] = []
        for i, a in enumerate(self.args):
            if i > 0 and self.args[i - 1] == "--config":
                masked.append("<redacted>")
            else:
                masked.append(a)
        return f"OdooProcess(id={self.id!r}, pid={self.pid!r}, args={masked!r}, started_at={self.started_at!r})"


class ProcessStatus(msgspec.Struct):
    state: Literal["running", "exited"]
    returncode: int | None = None


class ReadinessResult(msgspec.Struct):
    ok: bool
    elapsed: float
    attempts: int
    final_status: str | None = None


class RestoreResult(msgspec.Struct):
    new_db: str
    source: Backup


class DropResult(msgspec.Struct):
    db: str
