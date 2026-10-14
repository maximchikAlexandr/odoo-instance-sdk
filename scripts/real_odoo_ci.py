"""Runtime evidence and timing hooks for real-Odoo pytest jobs."""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from odoo_instance_sdk.internal.sanitize import sanitize_last_error, sanitize_terminal_text
from scripts import real_odoo_timing

_RUNTIMES: list[Any] = []
_RESOURCE_SNAPSHOTS: list[list[dict[str, object]]] = []
_SOURCE_CACHE_CONSUMED: list[bool] = []
_ENABLED = False


def _evidence_root() -> Path:
    configured = os.environ.get("ODCLI_E2E_EVIDENCE_ROOT", ".artifacts/real-odoo-e2e")
    path = Path(configured)
    return path if path.is_absolute() else Path.cwd() / path


def _bounded_tail(value: str) -> bytes:
    clean = sanitize_terminal_text(sanitize_last_error(value) or "", preserve_newlines=True)
    return clean.encode("utf-8")[-2 * 1024 * 1024 :]


def _append_log(name: str, value: str) -> None:
    path = _evidence_root() / name
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("ab") as stream:
        stream.write(_bounded_tail(value))
        stream.write(b"\n")
    if path.stat().st_size > 2 * 1024 * 1024:
        path.write_bytes(path.read_bytes()[-2 * 1024 * 1024 :])
    path.chmod(0o600)


def _capture_service_logs(runtime: Any) -> None:
    command = [
        "docker",
        "compose",
        "--project-name",
        runtime.topology.project_name,
        "--file",
        str(runtime.compose_file),
        "logs",
        "--no-color",
        "--tail",
        "200",
    ]
    services = ("source_odoo",) if runtime.scope == "source" else ("target_init",)
    postgres_services = ("source_postgres",) if runtime.scope == "source" else ("target_postgres",)
    for name, selected_services in (
        ("compose.log", ()),
        ("odoo.log", services),
        ("postgres.log", postgres_services),
    ):
        try:
            result = subprocess.run(
                [*command, *selected_services],
                cwd=runtime.compose_file.parent,
                capture_output=True,
                check=False,
                text=True,
                timeout=30.0,
            )
            _append_log(name, result.stdout + result.stderr)
        except (OSError, subprocess.SubprocessError) as error:
            _append_log(name, str(error))
    if runtime.scope == "target":
        runtime_root = getattr(runtime, "root", None)
        if runtime_root is None:
            return
        target_logs = sorted(Path(runtime_root).rglob("odoo.log"))
        for path in target_logs:
            if path.is_file() and not path.is_symlink():
                try:
                    _append_log(
                        "target-odoo.log", path.read_text(encoding="utf-8", errors="replace")
                    )
                except OSError as error:
                    _append_log("target-odoo.log", str(error))


def _snapshot_resources(runtime: Any) -> list[dict[str, object]]:
    return [
        {
            "kind": record.kind,
            "name": record.name,
            "metadata": dict(record.metadata),
        }
        for record in runtime.ledger.records
    ]


def _audit(runtime: Any) -> dict[str, object]:
    from tests.integration.real_odoo.cleanup import LeakError, audit_no_leaks

    try:
        report = audit_no_leaks(
            runtime.run_id,
            compose_project=runtime.topology.project_name,
            runtime_root=runtime.root,
            ports=(
                runtime.topology.source_postgres_port,
                runtime.topology.target_postgres_port,
                runtime.topology.source_odoo_port,
                runtime.reservations[3].port,
            ),
            catalog_path=runtime.root / "catalog.sqlite3",
            filestore_paths=(runtime.root / "source-data", runtime.root / "target-data"),
        )
    except LeakError as error:
        report = error.report
        return {
            "run_id": runtime.run_id,
            "state": "leaked",
            "leaks": {name: list(values) for name, values in report.leaks.items()},
        }
    except BaseException as error:
        return {
            "run_id": runtime.run_id,
            "state": "error",
            "error": sanitize_last_error(str(error)) or "audit failed",
            "leaks": {},
        }
    return {
        "run_id": runtime.run_id,
        "state": "clean" if report.clean else "leaked",
        "leaks": {name: list(values) for name, values in report.leaks.items()},
    }


def _source_cache_path() -> Path | None:
    cache = os.environ.get(
        "ODCLI_E2E_ODOO_SOURCE_CACHE",
        os.environ.get("ODCLI_E2E_SOURCE_CACHE"),
    )
    if not cache:
        return None
    cache_path = Path(cache)
    if not cache_path.is_absolute():
        cache_path = Path.cwd() / cache_path
    return cache_path


def _pinned_source_head(project: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(project), "rev-parse", "HEAD"],
            capture_output=True,
            check=False,
            text=True,
            timeout=30.0,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return (
        result.returncode == 0
        and result.stdout.strip() == "cd992ceebbaf343c03e1941d39cfe423d35ba6c6"
    )


def _source_cache_origin(project: Path, cache_path: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(project), "config", "--get", "remote.origin.url"],
            capture_output=True,
            check=False,
            text=True,
            timeout=30.0,
        )
        return (
            result.returncode == 0
            and bool(result.stdout.strip())
            and Path(result.stdout.strip()).expanduser().resolve() == cache_path.resolve()
        )
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return False


def _checkout_consumed_source_cache(root: Path, cache_path: Path) -> bool:
    alternate = (cache_path / "objects").resolve()
    if not root.is_dir():
        return False

    projects = [root] if (root / ".git").exists() else []
    projects.extend(
        git_path.parent
        for git_path in root.rglob(".git")
        if git_path.is_dir() or git_path.is_file()
    )
    for project in dict.fromkeys(projects):
        if _pinned_source_head(project) and _source_cache_origin(project, cache_path):
            return True
    for alternate_file in root.rglob("alternates"):
        if alternate_file.parent.name != "info":
            continue
        try:
            project = alternate_file.parents[3]
            lines = alternate_file.read_text(encoding="utf-8").splitlines()
            alternate_sources = {
                (alternate_file.parent / line).resolve()
                if not Path(line).is_absolute()
                else Path(line).resolve()
                for line in lines
                if line
            }
            if alternate in alternate_sources and _pinned_source_head(project):
                return True
        except (OSError, RuntimeError, subprocess.SubprocessError, IndexError):
            continue
    return False


def _runtime_consumed_source_cache(runtime: Any) -> bool:
    cache_path = _source_cache_path()
    return cache_path is not None and _checkout_consumed_source_cache(
        Path(runtime.root), cache_path
    )


def _remember_source_cache_consumption(runtime_root: Path) -> None:
    cache_path = _source_cache_path()
    if cache_path is not None and _checkout_consumed_source_cache(runtime_root, cache_path):
        _SOURCE_CACHE_CONSUMED.append(True)


def _source_cache_consumed() -> bool:
    """Report whether the real source-backed OdCLI checkout used the bare cache."""
    return _source_cache_path() is not None and any(_SOURCE_CACHE_CONSUMED)


def _write_command_matrix() -> None:
    from scripts.check_e2e_contract import MATRIX
    from tests.integration.real_odoo.contracts import render_matrix_document
    from tests.unit.test_cli_output_modes import PUBLIC_LEAF_CASES

    content = render_matrix_document(MATRIX.read_text(encoding="utf-8"), PUBLIC_LEAF_CASES)
    encoded = content.encode("utf-8")
    if len(encoded) > 2 * 1024 * 1024:
        raise ValueError("generated command matrix exceeds evidence text limit")
    path = _evidence_root() / "command-matrix.md"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(encoded)
    path.chmod(0o600)


def _write_resource_manifest() -> None:
    if not _RUNTIMES:
        return
    audits = [_audit(runtime) for runtime in _RUNTIMES]
    clean = all(audit["state"] == "clean" for audit in audits)
    manifest = {
        "schema": "odcli-real-odoo-resource-v1",
        "resources": [resource for snapshot in _RESOURCE_SNAPSHOTS for resource in snapshot],
        "audit": {
            "state": "clean" if clean else "failed",
            "leaks": [] if clean else audits,
            "runs": audits,
        },
        "source_cache_consumed": _source_cache_consumed(),
    }
    path = _evidence_root() / "resource-manifest.json"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


@contextmanager
def capture_cleanup(runtime: Any) -> Iterator[None]:
    """Capture evidence around the fixture's explicit cleanup boundary."""
    if not _ENABLED:
        yield
        return
    _RUNTIMES.append(runtime)
    _RESOURCE_SNAPSHOTS.append(_snapshot_resources(runtime))
    _remember_source_cache_consumption(Path(runtime.root))
    _capture_service_logs(runtime)
    configured = os.environ.get("ODCLI_E2E_TIMING_FILE")
    started = time.monotonic()
    try:
        yield
    finally:
        try:
            _write_resource_manifest()
        finally:
            if configured:
                real_odoo_timing.record(Path(configured), "cleanup", started, time.monotonic())


def pytest_configure(config: object) -> None:
    global _ENABLED  # noqa: PLW0603
    del config
    _ENABLED = True
    _write_command_matrix()
