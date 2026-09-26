"""``odcli update`` self-upgrade flow over uv-tool installs.

This module implements the nine-phase ``update_command()`` SDK primitive
described in OpenSpec change ``address-alpha-testing-defects-74`` item 16.
Expression is intentionally absent: every phase is a concrete typed action or
a frozen ``ProcessStep`` through ``internal/proc``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.metadata import Distribution, PackageNotFoundError, distribution
from pathlib import Path
from types import TracebackType
from typing import Literal, cast
from urllib.parse import unquote, urlsplit

import msgspec

from odoo_instance_sdk.exceptions import (
    PreflightFailedError,
    UnsupportedInstallError,
    UpdateError,
    UpdateIncompleteError,
)
from odoo_instance_sdk.execution import Command, JsonValue
from odoo_instance_sdk.internal.paths import get_locks_dir, get_user_root
from odoo_instance_sdk.internal.proc import (
    ProcessExecutor,
    ProcessResult,
)
from odoo_instance_sdk.internal.self_update_recovery import (
    UpdateRecoveryState,
)
from odoo_instance_sdk.internal.self_update_recovery import (
    inspect_update_recovery_state as _inspect_update_recovery_state,
)
from odoo_instance_sdk.models.update import UpdateOutcome, UpdatePhaseDuration, UpdateResult

_PACKAGE_NAME = "odoo-instance-sdk"
_SOURCE_REPO = "https://github.com/maximchikAlexandr/odoo-instance-sdk.git"
_REPO_SLUG = "maximchikAlexandr/odoo-instance-sdk"
_REPO_URL = f"git+{_SOURCE_REPO}"
_FULL_SHA_RE = re.compile(r"\b[0-9a-f]{40}\b", re.IGNORECASE)
_HEX40_RE = re.compile(r"^[0-9a-f]{40}$")
_DEFAULT_REF = "main"
_MIN_PYTHON = (3, 12)
_SUPPORTED_PLATFORMS = frozenset({"darwin", "linux", "win32", "cygwin"})
_RESERVE_FRACTION = 0.10
_RESERVE_FLOOR = 1024 * 1024 * 1024
_MAINTENANCE_ENV = "ODCLI_MAINTENANCE"
_JOURNAL_VERSION = 1
_JOURNAL_PHASES = frozenset(
    {"inspect", "preflight", "quiesce", "snapshot", "install", "migrate", "verify"}
)
_MANUAL_INSTALL_ARGV: tuple[str, ...] = (
    "uv",
    "tool",
    "install",
    "--force",
    f"{_PACKAGE_NAME} @ {_REPO_URL}@main",
)


@dataclass(frozen=True, slots=True)
class InstalledProvenance:
    """The resolved provenance of the currently installed OdCLI package."""

    version: str
    source_repo: str | None
    commit_id: str | None
    requested_revision: str | None
    is_uv_tool_vcs: bool
    uv_tool_bin_path: Path | None
    uv_tool_env_path: Path | None
    manual_argv: tuple[str, ...] | None


def _is_exact_supported_https_repo(source_repo: str) -> bool:
    raw = source_repo.removeprefix("git+")
    parsed = urlsplit(raw)
    return parsed.scheme == "https" and raw.rstrip("/").removesuffix(
        ".git"
    ) == _SOURCE_REPO.removesuffix(".git")


def _local_source_repo_path(source_repo: str | None) -> Path | None:
    if not source_repo:
        return None
    raw = source_repo.removeprefix("git+")
    if not raw.startswith("file://"):
        return None
    parsed = urlsplit(raw)
    if (
        parsed.scheme != "file"
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or not parsed.path
    ):
        return None
    return Path(unquote(parsed.path))


def _is_supported_source_origin(origin: str) -> bool:
    return _is_exact_supported_https_repo(origin.strip())


def _is_supported_source_repo(source_repo: str | None) -> bool:
    return _canonical_supported_source_repo(source_repo) is not None


def _canonical_supported_source_repo(source_repo: str | None) -> str | None:
    """Return the fixed credential-free origin for an accepted provenance URL."""
    if not source_repo:
        return None
    raw = source_repo.removeprefix("git+")
    if raw.startswith("file://"):
        return None
    if not _is_exact_supported_https_repo(raw):
        return None
    return _SOURCE_REPO


def _install_requirement(ref: str) -> str:
    return f"{_PACKAGE_NAME} @ {_REPO_URL}@{ref}"


def _failure_result(
    outcome: UpdateOutcome,
    *,
    provenance: InstalledProvenance | None = None,
    target_sha: str | None = None,
    manual_argv: tuple[str, ...] | None = None,
    recovery_argv: tuple[str, ...] | None = None,
    rollback_outcome: str | None = None,
    snapshot_state: str = "absent",
    journal_state: str = "absent",
    next_step: str | None = None,
    phase_durations: tuple[UpdatePhaseDuration, ...] = (),
) -> UpdateResult:
    return UpdateResult(
        outcome=outcome,
        source_repo=provenance.source_repo if provenance else None,
        previous_version=provenance.version if provenance else None,
        previous_sha=provenance.commit_id if provenance else None,
        target_sha=target_sha,
        executable_path=(
            str(provenance.uv_tool_bin_path) if provenance and provenance.uv_tool_bin_path else None
        ),
        tool_env_path=(
            str(provenance.uv_tool_env_path) if provenance and provenance.uv_tool_env_path else None
        ),
        snapshot_state=snapshot_state,
        journal_state=journal_state,
        rollback_outcome=rollback_outcome,
        next_step=next_step,
        manual_argv=manual_argv,
        recovery_argv=recovery_argv,
        phase_durations=phase_durations,
    )


def _assert_runtime_environment(provenance: InstalledProvenance) -> None:
    executable = Path(sys.executable)
    if not executable.is_file():
        raise UnsupportedInstallError(
            f"current Python interpreter is missing: {sys.executable}",
            manual_argv=_MANUAL_INSTALL_ARGV,
        )
    if shutil.which("uv") is None:
        raise UnsupportedInstallError(
            "uv is not available on PATH",
            manual_argv=_MANUAL_INSTALL_ARGV,
        )
    user_root = get_user_root(ensure_exists=True)
    probe = user_root / ".update-write-probe"
    try:
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise UnsupportedInstallError(
            f"cannot write to user root {user_root}",
            manual_argv=_MANUAL_INSTALL_ARGV,
        ) from exc
    if provenance.uv_tool_bin_path is None or not provenance.uv_tool_bin_path.is_file():
        raise UnsupportedInstallError(
            "uv-tool odcli executable is missing from the expected layout",
            manual_argv=_MANUAL_INSTALL_ARGV,
        )
    if provenance.uv_tool_env_path is not None:
        env_root = provenance.uv_tool_env_path
        if not env_root.is_dir():
            raise UnsupportedInstallError(
                f"uv-tool environment path is missing: {env_root}",
                manual_argv=_MANUAL_INSTALL_ARGV,
            )


def _read_direct_url_payload(dist: Distribution) -> dict[str, JsonValue] | None:
    raw = dist.read_text("direct_url.json")
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _normalize_legacy_direct_url(provenance: InstalledProvenance) -> None:
    """Keep the installed VCS URL readable by pre-fix update parents."""
    if provenance.source_repo is None or provenance.commit_id is None:
        return
    try:
        dist = distribution(_PACKAGE_NAME)
    except PackageNotFoundError:
        return
    files = dist.files
    if files is None:
        return
    for file_path in files:
        if Path(str(file_path)).name != "direct_url.json":
            continue
        metadata_path = Path(str(dist.locate_file(file_path)))
        payload = _read_direct_url_payload(dist)
        if payload is None:
            return
        url = payload.get("url")
        vcs_info = payload.get("vcs_info")
        if (
            not isinstance(url, str)
            or url.startswith("git+")
            or not isinstance(vcs_info, dict)
            or vcs_info.get("vcs") != "git"
        ):
            return
        payload["url"] = f"git+{provenance.source_repo}@{provenance.commit_id}"
        try:
            metadata_path.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            raise UpdateError("cannot persist legacy-compatible VCS provenance") from exc
        return


def _uv_tool_bin_dir() -> Path | None:
    """Return the uv tool bin directory if discoverable from PATH layout."""
    home = Path.home().expanduser()
    candidates = (
        home / ".local" / "bin",
        Path("/usr/local/bin"),
        Path("/usr/bin"),
    )
    for candidate in candidates:
        try:
            if candidate.is_dir():
                return candidate
        except OSError:
            continue
    return None


def _uv_tool_executable_path() -> Path | None:
    """Return the absolute path of the ``odcli`` script installed by uv tool."""
    exe = os.environ.get("ODCLI_UV_TOOL_EXECUTABLE")
    if exe:
        path = Path(exe).expanduser()
        if path.is_absolute() and path.exists():
            return path
    bin_dir = _uv_tool_bin_dir()
    if bin_dir is None:
        return None
    candidate = bin_dir / "odcli"
    try:
        if candidate.exists():
            return candidate.resolve()
    except OSError:
        return None
    return None


def _uv_tool_env_path(dist: Distribution) -> Path | None:
    """Return the uv tool environment path from the distribution file tree."""
    files = dist.files
    if files is None:
        return None
    for file_path in files:
        resolved = Path(str(file_path))
        if resolved.name == "odcli" and "tools" in resolved.parts:
            try:
                tools_index = resolved.parts.index("tools")
                return Path(*resolved.parts[: tools_index + 2])
            except ValueError:
                continue
    return None


def _parse_vcs_direct_url(
    payload: dict[str, JsonValue] | None,
) -> tuple[str | None, str | None, str | None, bool]:
    source_repo: str | None = None
    commit_id: str | None = None
    requested_revision: str | None = None
    is_vcs = False
    if payload is None:
        return source_repo, commit_id, requested_revision, is_vcs
    url = payload.get("url")
    vcs_info = payload.get("vcs_info")
    if isinstance(vcs_info, dict) and vcs_info.get("vcs") == "git":
        is_vcs = True
    if isinstance(url, str):
        if url.startswith("git+"):
            is_vcs = True
            source_repo = url.split("+", 1)[1].split("@", 1)[0]
            if "@" in url:
                requested_revision = url.split("@", 1)[1] or None
        elif is_vcs:
            # uv emits a bare repository URL alongside ``vcs_info`` for
            # installed GitHub revisions.  Keep that PEP 610 form intact;
            # the repository allow-list below remains authoritative.
            source_repo = url
    if isinstance(vcs_info, dict):
        raw_commit = vcs_info.get("commit_id")
        if isinstance(raw_commit, str) and _HEX40_RE.match(raw_commit):
            commit_id = raw_commit.lower()
        raw_rev = vcs_info.get("requested_revision")
        if isinstance(raw_rev, str) and raw_rev:
            requested_revision = raw_rev
    return source_repo, commit_id, requested_revision, is_vcs


def read_uv_tool_direct_url(
    package_name: str = _PACKAGE_NAME,
) -> InstalledProvenance:
    """Inspect the installed package metadata and on-disk layout in-process."""
    try:
        dist = distribution(package_name)
    except PackageNotFoundError as exc:
        raise UnsupportedInstallError(
            "odoo-instance-sdk is not installed",
            manual_argv=_MANUAL_INSTALL_ARGV,
        ) from exc
    source_repo, commit_id, requested_revision, is_vcs = _parse_vcs_direct_url(
        _read_direct_url_payload(dist),
    )
    if not is_vcs:
        raise UnsupportedInstallError(
            "odcli update supports only uv-tool VCS installs",
            manual_argv=_MANUAL_INSTALL_ARGV,
        )
    canonical_source_repo = _canonical_supported_source_repo(source_repo)
    local_source_repo = _local_source_repo_path(source_repo)
    if canonical_source_repo is None and local_source_repo is None:
        raise UnsupportedInstallError(
            f"unsupported source repository; only {_REPO_SLUG} uv-tool installs are supported",
            manual_argv=_MANUAL_INSTALL_ARGV,
        )
    provenance = InstalledProvenance(
        version=dist.version,
        source_repo=canonical_source_repo or source_repo,
        commit_id=commit_id,
        requested_revision=requested_revision,
        is_uv_tool_vcs=True,
        uv_tool_bin_path=_uv_tool_executable_path(),
        uv_tool_env_path=_uv_tool_env_path(dist),
        manual_argv=None,
    )
    _assert_runtime_environment(provenance)
    return provenance


def _is_full_sha(value: str) -> bool:
    return bool(_HEX40_RE.match(value))


def _install_argv(ref: str) -> tuple[str, ...]:
    return ("uv", "tool", "install", "--force", _install_requirement(ref))


def _resolve_argv(ref: str) -> tuple[str, ...]:
    """Resolve a mutable branch or tag without modifying the uv tool."""
    return (
        "git",
        "ls-remote",
        "--exit-code",
        _SOURCE_REPO,
        f"refs/heads/{ref}",
        f"refs/tags/{ref}",
        f"refs/tags/{ref}^{{}}",
    )


def _maintenance_argv(executable: str | Path) -> tuple[str, ...]:
    return (str(executable), "update", "--format", "json")


def _update_lock_path() -> Path:
    return get_locks_dir(ensure_exists=False) / "odcli-update.lock"


def _update_journal_path() -> Path:
    return get_user_root(ensure_exists=False) / "update" / "journal.json"


def _update_snapshot_dir() -> Path:
    return get_user_root(ensure_exists=False) / "update" / "snapshot"


def _read_journal(path: Path) -> dict[str, JsonValue] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def inspect_update_recovery_state() -> UpdateRecoveryState:
    return _inspect_update_recovery_state(get_user_root(ensure_exists=False))


def unfinished_update_journal() -> dict[str, JsonValue] | None:
    """Return the unfinished update journal payload when one is present."""
    journal = _read_journal(_update_journal_path())
    if journal is None:
        return None
    phase = journal.get("phase")
    if phase == "verify" or phase not in _JOURNAL_PHASES:
        return None
    return journal


def assert_update_not_blocking(command_name: str) -> None:
    """Refuse normal commands while an update journal is unfinished."""
    journal = unfinished_update_journal()
    if journal is None:
        return
    raise UpdateIncompleteError(
        f"odcli update is incomplete (phase={journal.get('phase')!r}); "
        f"run `odcli update` to resume before `{command_name}`",
    )


def _write_journal(path: Path, payload: Mapping[str, JsonValue]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _clear_journal(path: Path) -> None:
    with _suppress_file_not_found():
        path.unlink()


def _snapshot_metadata(provenance: InstalledProvenance, ref: str) -> dict[str, JsonValue]:
    return {
        "version": _JOURNAL_VERSION,
        "previous_version": provenance.version,
        "package_revision": provenance.version,
        "previous_sha": provenance.commit_id,
        "install_requirement": _install_requirement(ref),
        "source_repo": provenance.source_repo,
        "target_ref": ref,
        "snapshot_sha": provenance.commit_id,
    }


def _write_snapshot(snapshot_dir: Path, metadata: Mapping[str, JsonValue]) -> None:
    snapshot_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    (snapshot_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    catalog = get_user_root(ensure_exists=False) / "catalog.sqlite3"
    if catalog.is_file():
        shutil.copy2(catalog, snapshot_dir / "catalog.sqlite3")


def _restore_snapshot(snapshot_dir: Path) -> None:
    catalog = snapshot_dir / "catalog.sqlite3"
    if not catalog.is_file():
        return
    target = get_user_root(ensure_exists=False) / "catalog.sqlite3"
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    shutil.copy2(catalog, target)


def _clear_snapshot(snapshot_dir: Path) -> None:
    with _suppress_file_not_found():
        shutil.rmtree(snapshot_dir)


class _suppress_file_not_found:
    """Suppress FileNotFoundError for best-effort cleanup paths."""

    def __enter__(self) -> _suppress_file_not_found:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        return exc_type is FileNotFoundError


def _catalog_schema_version() -> str:
    catalog = get_user_root(ensure_exists=False) / "catalog.sqlite3"
    if not catalog.is_file():
        return "none"
    try:
        conn = sqlite3.connect(str(catalog))
    except sqlite3.Error:
        return "unreadable"
    try:
        exists = (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='alembic_version'"
            ).fetchone()
            is not None
        )
        if not exists:
            return "none"
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        return str(row[0]) if row is not None else "none"
    except sqlite3.Error:
        return "unreadable"
    finally:
        conn.close()


def _storage_migration_state() -> str:
    journal = get_user_root(ensure_exists=False) / "storage-migration.json"
    if not journal.is_file():
        return "absent"
    try:
        payload = json.loads(journal.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "unreadable"
    if isinstance(payload, dict):
        stage = payload.get("stage")
        if isinstance(stage, str):
            return stage
    return "unreadable"


def _validate_catalog_migration_path() -> None:
    from alembic.script import ScriptDirectory

    from odoo_instance_sdk.storage.catalog_migrate import (
        _alembic_config,
        _assert_single_head,
        catalog_revision,
    )

    head = _assert_single_head()
    catalog_path = get_user_root(ensure_exists=False) / "catalog.sqlite3"
    if not catalog_path.is_file():
        return
    conn = sqlite3.connect(str(catalog_path))
    try:
        current = catalog_revision(conn)
    finally:
        conn.close()
    if current is None or current == head:
        return
    script = ScriptDirectory.from_config(_alembic_config(catalog_path))
    try:
        tuple(script.iterate_revisions(head, current))
    except Exception as exc:
        raise PreflightFailedError(
            f"no catalog migration path from {current!r} to {head!r}",
        ) from exc


def _validate_storage_migration_path() -> None:
    state = _storage_migration_state()
    if state in {"absent", "complete"}:
        return
    raise PreflightFailedError(f"storage migration is not resumable: {state}")


def _preflight_disk_check() -> str | None:
    user_root = get_user_root(ensure_exists=False)
    try:
        usage = shutil.disk_usage(user_root)
    except OSError as exc:
        raise PreflightFailedError(f"cannot measure free space at {user_root}") from exc
    reserve = max(_RESERVE_FLOOR, int(usage.free * _RESERVE_FRACTION))
    available = usage.free - reserve
    if available <= 0:
        return (
            f"insufficient free space at {user_root}: "
            f"measured {usage.free} bytes free, reserve {reserve} bytes, "
            f"available {available} bytes after reserve"
        )
    return None


def _run_coordinator_migrations() -> tuple[tuple[str, ...], tuple[str, ...], dict[str, str]]:
    from odoo_instance_sdk.internal.storage_migration import migrate_storage
    from odoo_instance_sdk.storage.catalog_migrate import CATALOG_REVISION, ensure_catalog_migrated

    catalog_path = get_user_root(ensure_exists=False) / "catalog.sqlite3"
    executed: list[str] = []
    skipped: list[str] = []
    if catalog_path.is_file():
        ensure_catalog_migrated(catalog_path)
        executed.append(f"catalog:{CATALOG_REVISION}")
    else:
        skipped.append("catalog:absent")
    result = migrate_storage()
    if result.state == "complete":
        if result.migrated:
            executed.extend(f"storage:{name}" for name in result.migrated)
        else:
            skipped.append("storage:none")
    final = {
        "catalog": _catalog_schema_version(),
        "storage": _storage_migration_state(),
    }
    return tuple(executed), tuple(skipped), final


def _expected_sha_for_target_ref(target_ref: str | None) -> str | None:
    if target_ref is not None and _is_full_sha(target_ref):
        return target_ref.lower()
    return None


def _process_output_text(result: ProcessResult) -> str:
    stdout = (
        result.stdout
        if isinstance(result.stdout, str)
        else (result.stdout or b"").decode("utf-8", "replace")
    )
    stderr = (
        result.stderr
        if isinstance(result.stderr, str)
        else (result.stderr or b"").decode("utf-8", "replace")
    )
    return f"{stdout}\n{stderr}"


def _install_reached_target(*, ref: str, output: str, previous_sha: str | None) -> bool:
    try:
        installed = read_uv_tool_direct_url()
    except UnsupportedInstallError:
        return False
    commit_id = installed.commit_id
    if commit_id is None:
        return False
    if _is_full_sha(ref):
        return commit_id == ref.lower()
    extracted = _extract_target_sha(ref, output)
    if extracted is not None:
        return commit_id == extracted
    return previous_sha is not None and commit_id != previous_sha


def _ensure_maintenance_path() -> None:
    """Recover a usable command path for legacy hermetic maintenance children."""
    if shutil.which("uv") is not None:
        return
    candidates = (
        Path.home() / ".local" / "bin",
        Path.home() / "AppData" / "Local" / "bin",
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
        Path("/usr/bin"),
    )
    entries = [str(path) for path in candidates if path.is_dir()]
    if entries:
        entries.append(os.defpath)
        os.environ["PATH"] = os.pathsep.join(entries)


def prepare_maintenance_environment() -> None:
    """Recover HOME and PATH when a legacy child launched hermetically."""
    if not os.environ.get("HOME"):
        # Keep the uv-tool path symlink intact; resolving it would jump to the
        # host's shared Python and lose the isolated tool home.
        executable = Path(sys.executable)
        for index, parent in enumerate(executable.parents):
            if parent.name == ".local" and index > 0:
                os.environ["HOME"] = str(parent.parent)
                break
    _ensure_maintenance_path()


def _verify_installed_revision(
    expected_sha: str | None,
    *,
    target_ref: str | None = None,
    version_result: ProcessResult | None = None,
) -> None:
    provenance = read_uv_tool_direct_url()
    expected = expected_sha or _expected_sha_for_target_ref(target_ref)
    if expected and provenance.commit_id != expected:
        raise UpdateError(
            f"installed revision {provenance.commit_id!r} does not match {expected!r}",
        )
    if provenance.uv_tool_bin_path is None:
        raise UpdateError("uv-tool odcli executable is missing after update")
    if version_result is not None and version_result.returncode != 0:
        raise UpdateError("odcli --version failed after update")


def _verify_doctor_startup() -> None:
    import odoo_instance_sdk.cli  # noqa: F401
    from odoo_instance_sdk.internal.doctor.manifest import (
        STATUS_ERROR,
        DoctorReport,
        _check_uv,
    )

    report = DoctorReport()
    _check_uv(report)
    for check in report.checks:
        if check.status == STATUS_ERROR:
            raise UpdateError(f"doctor startup check failed: {check.detail}")


def _verify_reached_schema_versions(final_versions: dict[str, str]) -> None:
    from odoo_instance_sdk.storage.catalog_migrate import CATALOG_REVISION

    catalog = final_versions.get("catalog", "none")
    if catalog not in {"none", CATALOG_REVISION}:
        raise UpdateError(f"catalog schema version {catalog!r} did not reach {CATALOG_REVISION!r}")
    storage = final_versions.get("storage", "absent")
    if storage not in {"absent", "complete"}:
        raise UpdateError(f"storage migration did not complete: {storage!r}")


def _record_maintenance_pid() -> None:
    journal_path = _update_journal_path()
    journal = _read_journal(journal_path)
    if journal is None or journal.get("phase") != "migrate":
        return
    payload = dict(journal)
    payload["maintenance_pid"] = os.getpid()
    _write_journal(journal_path, payload)


def _maintenance_run() -> UpdateResult:
    """Run migrations and verify inside the maintenance child process."""
    _record_maintenance_pid()
    provenance = read_uv_tool_direct_url()
    journal = _read_journal(_update_journal_path())
    target_ref = journal.get("target_ref") if journal is not None else None
    target_ref_str = target_ref if isinstance(target_ref, str) else None
    executed, skipped, final_versions = _run_coordinator_migrations()
    _verify_doctor_startup()
    _verify_reached_schema_versions(final_versions)
    _normalize_legacy_direct_url(provenance)
    expected_target = _expected_sha_for_target_ref(target_ref_str)
    return UpdateResult(
        outcome="updated",
        source_repo=provenance.source_repo,
        previous_version=provenance.version,
        target_version=provenance.version,
        final_version=provenance.version,
        previous_sha=provenance.commit_id,
        target_sha=expected_target or provenance.commit_id,
        final_sha=provenance.commit_id,
        executable_path=str(provenance.uv_tool_bin_path) if provenance.uv_tool_bin_path else None,
        tool_env_path=str(provenance.uv_tool_env_path) if provenance.uv_tool_env_path else None,
        executed_migration_ids=executed,
        skipped_migration_ids=skipped,
        final_schema_versions=final_versions,
        snapshot_state="cleared",
        journal_state="cleared",
        rollback_outcome=None,
        next_step=None,
    )


def is_maintenance_mode() -> bool:
    """Return True when this process was launched as the maintenance child."""
    return os.environ.get(_MAINTENANCE_ENV) == "1"


def run_maintenance() -> int:
    """Entry point for the maintenance child; writes one JSON document."""
    prepare_maintenance_environment()
    result = _maintenance_run()
    sys.stdout.write(json.dumps(msgspec.to_builtins(result), indent=2, sort_keys=True))
    sys.stdout.write("\n")
    sys.stdout.flush()
    return 0


def _extract_target_sha(ref: str, uv_output: str) -> str | None:
    if _is_full_sha(ref):
        return ref.lower()
    matches = _FULL_SHA_RE.findall(uv_output)
    if not matches:
        return None
    return str(matches[-1]).lower()


def _phase_durations(record: dict[str, float]) -> tuple[UpdatePhaseDuration, ...]:
    return tuple(
        UpdatePhaseDuration(
            phase=cast(
                "Literal['inspect','resolve','preflight','quiesce','snapshot','install','migrate','verify','commit']",
                phase,
            ),
            duration_seconds=round(record[phase], 6),
        )
        for phase in sorted(record)
    )


def update_command(
    *,
    ref: str = _DEFAULT_REF,
    check: bool = False,
    dry_run: bool = False,
    allow_downgrade: bool = False,
    executor: ProcessExecutor | None = None,
    force_mutation: bool = False,
) -> Command[UpdateResult]:
    """Return the immutable ``Command[UpdateResult]`` for ``odcli update``."""
    from odoo_instance_sdk.internal.self_update_commands import (
        _build_already_current_command,
        _build_check_command,
        _build_failure_command,
        _build_mutating_command,
        _build_staged_command,
    )

    try:
        provenance = read_uv_tool_direct_url()
    except UnsupportedInstallError as exc:
        return _build_failure_command(
            _failure_result(
                "unsupported_install",
                manual_argv=exc.manual_argv,
                next_step=str(exc),
            ),
        )
    if check:
        return _build_check_command(ref=ref, provenance=provenance, executor=executor)
    recovery = inspect_update_recovery_state()
    if recovery.has_evidence and not recovery.valid:
        return _build_failure_command(
            _failure_result(
                "update_incomplete",
                provenance=provenance,
                target_sha=ref.lower() if _is_full_sha(ref) else None,
                snapshot_state=recovery.snapshot_state,
                journal_state=recovery.journal_state,
                next_step=recovery.diagnostic or "update recovery evidence is incomplete",
            )
        )
    journal_target = recovery.target_ref
    target_sha = (
        ref.lower()
        if journal_target is not None and _is_full_sha(ref) and ref.lower() != journal_target
        else journal_target or (ref.lower() if _is_full_sha(ref) else ref)
    )
    if (
        not dry_run
        and not force_mutation
        and provenance.commit_id is not None
        and target_sha == provenance.commit_id
        and not recovery.has_evidence
    ):
        return _build_already_current_command(provenance)
    if not _is_full_sha(target_sha):
        return _build_staged_command(
            ref=target_sha,
            provenance=provenance,
            executor=executor,
        )
    return _build_mutating_command(
        ref=target_sha,
        provenance=provenance,
        executor=executor,
        allow_downgrade=allow_downgrade,
    )


def update(
    *,
    ref: str = _DEFAULT_REF,
    check: bool = False,
    allow_downgrade: bool = False,
    executor: ProcessExecutor | None = None,
) -> UpdateResult:
    """Convenience entry point that delegates to ``update_command()``."""
    command = update_command(
        ref=ref,
        check=check,
        allow_downgrade=allow_downgrade,
        executor=executor,
    )
    result = command.run()
    if check or result.outcome != "updated" or result.target_sha is None:
        return result
    if any(step.step_id == "update.install" for step in command.plan.steps):
        return result
    return update_command(
        ref=result.target_sha,
        allow_downgrade=allow_downgrade,
        executor=executor,
        force_mutation=True,
    ).run()


def preflight_update_command(
    *,
    ref: str,
    allow_downgrade: bool = False,
    executor: ProcessExecutor | None = None,
) -> Command[UpdateResult]:
    """Capture the read-only preflight stage for an immutable target."""
    from odoo_instance_sdk.internal.self_update_commands import _build_preflight_command

    return _build_preflight_command(
        ref=ref,
        provenance=read_uv_tool_direct_url(),
        allow_downgrade=allow_downgrade,
        executor=executor,
    )


__all__ = [
    "InstalledProvenance",
    "UpdateRecoveryState",
    "assert_update_not_blocking",
    "inspect_update_recovery_state",
    "is_maintenance_mode",
    "preflight_update_command",
    "prepare_maintenance_environment",
    "read_uv_tool_direct_url",
    "run_maintenance",
    "unfinished_update_journal",
    "update",
    "update_command",
]
