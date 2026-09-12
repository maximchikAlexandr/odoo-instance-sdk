"""Runtime evidence and timing hooks for real-Odoo pytest jobs."""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from odoo_instance_sdk.internal.sanitize import sanitize_last_error, sanitize_terminal_text
from scripts import real_odoo_timing

_RUNTIMES: list[Any] = []
_RESOURCE_SNAPSHOTS: list[list[dict[str, object]]] = []
_ORIGINAL_FINALIZE: Callable[..., Any] | None = None


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
    for name, services in (
        ("compose.log", ()),
        ("odoo.log", ("source_odoo", "target_odoo", "target_init")),
        ("postgres.log", ("source_postgres", "target_postgres")),
    ):
        try:
            result = subprocess.run(
                [*command, *services],
                cwd=runtime.compose_file.parent,
                capture_output=True,
                check=False,
                text=True,
                timeout=30.0,
            )
            _append_log(name, result.stdout + result.stderr)
        except (OSError, subprocess.SubprocessError) as error:
            _append_log(name, str(error))


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


def _source_cache_consumed() -> bool:
    cache = os.environ.get("ODCLI_E2E_SOURCE_CACHE")
    checkout = os.environ.get("ODCLI_E2E_SOURCE_CHECKOUT")
    if not cache or not checkout:
        return False
    cache_path = Path(cache)
    checkout_path = Path(checkout)
    if not cache_path.is_absolute():
        cache_path = Path.cwd() / cache_path
    if not checkout_path.is_absolute():
        checkout_path = Path.cwd() / checkout_path
    checkout_bin = checkout_path / "odoo-bin"
    if not checkout_bin.is_file():
        return False
    try:
        commit = "cd992ceebbaf343c03e1941d39cfe423d35ba6c6"
        verified = subprocess.run(
            ["git", "--git-dir", str(cache_path), "cat-file", "-e", f"{commit}^{{commit}}"],
            capture_output=True,
            check=False,
            timeout=30.0,
        )
        source_file = subprocess.run(
            ["git", "--git-dir", str(cache_path), "show", f"{commit}:odoo-bin"],
            capture_output=True,
            check=False,
            timeout=30.0,
        )
        return (
            verified.returncode == 0
            and source_file.returncode == 0
            and source_file.stdout == checkout_bin.read_bytes()
        )
    except (OSError, subprocess.SubprocessError):
        return False


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


def _instrumented_finalize(runtime: Any, primary_failure: BaseException | None = None) -> None:
    _RUNTIMES.append(runtime)
    _RESOURCE_SNAPSHOTS.append(_snapshot_resources(runtime))
    _capture_service_logs(runtime)
    configured = os.environ.get("ODCLI_E2E_TIMING_FILE")
    started = time.monotonic()
    try:
        assert _ORIGINAL_FINALIZE is not None
        _ORIGINAL_FINALIZE(runtime, primary_failure)
    finally:
        if configured:
            real_odoo_timing.record(Path(configured), "cleanup", started, time.monotonic())
        if getattr(runtime, "scope", None) == "source":
            _write_resource_manifest()


def pytest_configure(_config: object) -> None:
    global _ORIGINAL_FINALIZE  # noqa: PLW0603
    if _ORIGINAL_FINALIZE is not None:
        return
    try:
        from tests.integration.real_odoo import conftest
    except ImportError:
        return
    _ORIGINAL_FINALIZE = conftest._finalize
    conftest._finalize = _instrumented_finalize
