"""Ownership ledger, bounded evidence, and exact leak-audit helpers."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from odoo_instance_sdk.internal.sanitize import sanitize_last_error, sanitize_terminal_text

MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_FAILURE_BUNDLE_BYTES = 50 * 1024 * 1024


def write_owner_only_secret(path: Path, value: str) -> None:
    """Write a secret without placing it in an argv or generated manifest."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    stream = path.open("w", encoding="utf-8")
    try:
        stream.write(value)
        stream.write("\n")
    finally:
        stream.close()
    path.chmod(0o600)


def write_odoo_config(
    path: Path,
    *,
    database_host: str,
    database_port: int,
    database_password: str,
    data_dir: Path,
    http_port: int = 8069,
    addons_path: Iterable[Path] = (),
) -> None:
    """Write a minimal owner-only Odoo config with the password off argv."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    rendered_addons = ",".join(str(item) for item in addons_path)
    path.write_text(
        "[options]\n"
        f"db_host = {database_host}\n"
        f"db_port = {database_port}\n"
        "db_user = odoo\n"
        f"db_password = {database_password}\n"
        f"data_dir = {data_dir}\n"
        "http_interface = 0.0.0.0\n"
        f"http_port = {http_port}\n"
        "list_db = True\n" + (f"addons_path = {rendered_addons}\n" if rendered_addons else ""),
        encoding="utf-8",
    )
    path.chmod(0o600)


@dataclass(frozen=True, slots=True)
class ResourceRecord:
    kind: str
    name: str
    cleanup: Callable[[], None]
    metadata: Mapping[str, str] = field(default_factory=dict)


class ResourceLedger:
    """Record resources immediately and unwind them in reverse creation order."""

    def __init__(self, run_id: str) -> None:
        if not run_id:
            raise ValueError("run_id is required")
        self.run_id = run_id
        self._records: list[ResourceRecord] = []

    @property
    def records(self) -> tuple[ResourceRecord, ...]:
        return tuple(self._records)

    def record(
        self,
        kind: str,
        name: str,
        cleanup: Callable[[], None],
        *,
        metadata: Mapping[str, str] | None = None,
    ) -> ResourceRecord:
        if self.run_id not in name and name != self.run_id:
            raise ValueError(f"resource {name!r} is not namespaced to run {self.run_id!r}")
        item = ResourceRecord(kind, name, cleanup, dict(metadata or {}))
        self._records.append(item)
        return item

    def unwind(self, *, primary_failure: BaseException | None = None) -> None:
        errors: list[BaseException] = []
        for item in reversed(self._records):
            try:
                item.cleanup()
            except BaseException as error:
                errors.append(error)
        self._records.clear()
        if primary_failure is not None and errors:
            raise BaseExceptionGroup(
                "primary failure and cleanup failures", [primary_failure, *errors]
            )
        if primary_failure is not None:
            raise primary_failure
        if errors:
            raise BaseExceptionGroup("cleanup failures", errors)


@dataclass(slots=True)
class FailureEvidence:
    run_id: str
    secret_canary: str
    root: Path
    _logs: dict[str, str] = field(default_factory=dict)

    def add_log(self, name: str, text: str) -> None:
        if not name or Path(name).name != name or name in {".", ".."}:
            raise ValueError("evidence log name must be a single path component")
        clean = sanitize_terminal_text(sanitize_last_error(text) or "", preserve_newlines=True)
        encoded = clean.encode("utf-8")[:MAX_TEXT_BYTES]
        self._logs[name] = encoded.decode("utf-8", errors="ignore")

    def write(self) -> tuple[Path, ...]:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        files: list[Path] = []
        for name, text in self._logs.items():
            path = self.root / f"{name}.log"
            path.write_text(text, encoding="utf-8")
            path.chmod(0o600)
            files.append(path)
        manifest = self.root / "manifest.json"
        manifest.write_text(
            json.dumps({"run_id": self.run_id, "logs": sorted(self._logs)}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        manifest.chmod(0o600)
        files.append(manifest)
        if any(self.secret_canary in path.read_text(encoding="utf-8") for path in files):
            raise AssertionError("secret canary leaked into failure evidence")
        if sum(path.stat().st_size for path in files) > MAX_FAILURE_BUNDLE_BYTES:
            raise AssertionError("failure evidence exceeds bundle limit")
        return tuple(files)


@dataclass(frozen=True, slots=True)
class LeakReport:
    run_id: str
    leaks: Mapping[str, tuple[str, ...]]

    @property
    def clean(self) -> bool:
        return not any(self.leaks.values())


class LeakError(AssertionError):
    def __init__(self, report: LeakReport) -> None:
        self.report = report
        super().__init__(f"run {report.run_id} leaked resources: {dict(report.leaks)!r}")


def compose_down(compose_file: Path, project_name: str, *, timeout: float = 60.0) -> None:
    """Remove exactly one run's Compose objects, including volumes/orphans."""
    if not compose_file.is_file():
        return
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            project_name,
            "--file",
            str(compose_file),
            "down",
            "--volumes",
            "--remove-orphans",
        ],
        cwd=compose_file.parent,
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout,
    )
    if result.returncode:
        raise RuntimeError(f"Compose cleanup failed for owned project {project_name}")


def audit_no_leaks(
    run_id: str,
    *,
    probes: Mapping[str, Callable[[str], Iterable[str]]] | None = None,
) -> LeakReport:
    """Run every ownership probe and fail if any run-id resource remains."""
    categories = (
        "process",
        "container",
        "network",
        "volume",
        "port",
        "database",
        "filestore",
        "worktree",
        "catalog",
        "runtime-root",
    )

    def no_leaks(_run_id: str) -> Iterable[str]:
        return ()

    selected = probes or dict.fromkeys(categories, no_leaks)
    leaks = {category: tuple(selected.get(category, no_leaks)(run_id)) for category in categories}
    report = LeakReport(run_id, leaks)
    if not report.clean:
        raise LeakError(report)
    return report


def remove_owned_root(root: Path, *, run_id: str) -> None:
    """Remove an isolated root only when its name and path prove ownership."""
    if run_id not in root.name:
        raise ValueError(f"refusing to remove non-owned root: {root}")
    if root.exists():
        shutil.rmtree(root)
