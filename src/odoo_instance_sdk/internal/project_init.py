"""Project initialization helpers shared by the public SDK and CLI."""

from __future__ import annotations

import contextlib
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from odoo_instance_sdk.exceptions import InstanceConfigurationError
from odoo_instance_sdk.internal.dbprep.source import _planned_project_identity
from odoo_instance_sdk.internal.generated_config import (
    generate_config,
    project_generated_config_path,
    render_config,
)
from odoo_instance_sdk.internal.project_runtime import resolve_project_http_port
from odoo_instance_sdk.internal.urls import normalize_base_url
from odoo_instance_sdk.models import StartConfig
from odoo_instance_sdk.project import (
    ProjectConfig,
    TestInstanceProjectConfig,
    normalize_remote_name,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import JsonValue
    from odoo_instance_sdk.internal.proc import PreparedAction


def _resolve_source_config_path(root: Path, source: Path | None) -> Path | None:
    """Resolve the optional project source config path for init and repair."""
    source_path = (
        (root / source).resolve() if source is not None and not source.is_absolute() else source
    )
    if source_path is None:
        candidate = root / "odoo.conf"
        return candidate if candidate.is_file() else None
    return source_path if source_path.is_file() else None


def verify_project_owned_data_dir(project_root: Path, data_dir: str | Path) -> Path:
    """Verify a restore ``data_dir`` is a contained non-symlink project directory."""
    root = Path(project_root).resolve()
    path = Path(data_dir)
    if path.is_symlink() or not path.is_dir():
        raise InstanceConfigurationError(
            f"project-owned data_dir must be a regular directory, not a symlink: {path}"
        )
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise InstanceConfigurationError(
            f"project-owned data_dir must stay inside the project tree: {resolved}"
        ) from None
    return resolved


def project_owned_data_dir(project_root: Path) -> Path:
    """Return the canonical project-owned filestore directory.

    A self-contained Compose project keeps its Odoo ``data_dir`` inside the
    project tree so restore can record a proven path and ``db rm`` can clean
    the exact contained filestore without guessing by database name.
    """
    return (project_root.resolve() / ".odcli" / "filestore").resolve()


def manifest_dict(
    config: ProjectConfig, *, postgres_allocated: bool = False
) -> dict[str, JsonValue]:
    """Project one init manifest document for preview and machine output."""
    postgres: dict[str, JsonValue] | None = None
    if config.postgres is not None:
        postgres = {
            "mode": config.postgres.mode,
            "image": config.postgres.image,
            "port": config.postgres.port,
            "user": config.postgres.user,
            "allocated_port": postgres_allocated,
        }
    test_instance: dict[str, JsonValue] | None = None
    if config.test_instance is not None:
        test_instance = {
            "base_url": config.test_instance.base_url,
            "database": config.test_instance.database,
            "git_branch": config.test_instance.git_branch,
        }
    remote_instances: list[dict[str, JsonValue]] = [
        {
            "name": source.name,
            "base_url": normalize_base_url(source.base_url),
            "database": source.database,
            "git_branch": source.git_branch,
            "password_key": f"ODCLI_REMOTE_{normalize_remote_name(source.name).upper()}_MASTER_PASSWORD",
        }
        for source in sorted(config.remote_instances, key=lambda item: item.name)
    ]
    return {
        "odoo_bin": str(config.odoo_bin) if config.odoo_bin else None,
        "python": str(config.python) if config.python else None,
        "source_config": str(config.source_config) if config.source_config else None,
        "default_source_database": config.default_source_database,
        "default_base_ref": config.default_base_ref,
        "ticket_link_enabled": config.ticket_link_enabled is True,
        "ticket_base_url": config.ticket_base_url,
        "refresh_after_hours": config.refresh_after_hours,
        "test_instance": test_instance,
        "remote_instances": cast("JsonValue", remote_instances),
        "remote_password_keys": cast(
            "JsonValue", [item["password_key"] for item in remote_instances]
        ),
        "preferred_http_port": config.preferred_http_port,
        "requirements": list(config.requirements),
        "default_run_args": list(config.default_run_args),
        "runtime_cwd": str(config.runtime_cwd) if config.runtime_cwd else None,
        "postgres": postgres,
    }


def write_project_generated_config(project_path: Path, config: ProjectConfig) -> None:
    """Bind a Compose project config to its existing private cluster secret."""
    root = project_path.resolve()
    destination = project_generated_config_path(root)
    validate_generated_config_target(destination, project_root=root)
    source_path = _resolve_source_config_path(root, config.source_config)
    if config.source_config is not None and source_path is None:
        configured = Path(config.source_config).resolve()
        if configured != destination.resolve():
            raise InstanceConfigurationError("local source config is missing")
        source_path = None

    from odoo_instance_sdk.internal.postgres_compose import ensure_password_file
    from odoo_instance_sdk.resources.postgres import PostgresCluster

    cluster = PostgresCluster.from_project(root)
    password = ensure_password_file(cluster.password_file)
    source_start = (
        StartConfig.from_odoo_config(source_path) if source_path is not None else StartConfig()
    )
    postgres = config.postgres
    assert postgres is not None
    generate_config(
        source_path,
        destination,
        repo_root=root,
        worktree=root,
        http_interface=source_start.http_interface,
        http_port=resolve_project_http_port(config.preferred_http_port, source_start.http_port),
        db_name=config.default_source_database or source_start.db_name or "",
        db_host=cluster.endpoint_host,
        db_port=cluster.endpoint_port,
        db_user=postgres.user or "odoo",
        db_password=password,
        data_dir=project_owned_data_dir(root),
    )


def validate_generated_config_target(
    path: Path,
    *,
    project_root: Path | None = None,
) -> None:
    """Reject unsafe targets before any generated-config or secret write."""
    root = (project_root or path.parent.parent).resolve()
    expected = root / ".odcli" / "odoo.conf"
    if path.absolute() != expected:
        raise InstanceConfigurationError(
            f"generated config target is outside the project-owned path: {path}"
        )
    relative = path.relative_to(root)
    current = root
    try:
        root_stat = current.lstat()
    except FileNotFoundError as exc:
        raise InstanceConfigurationError(f"project root is missing: {root}") from exc
    _require_owned_directory(current, root_stat)
    for component in relative.parts[:-1]:
        current /= component
        try:
            parent = current.lstat()
        except FileNotFoundError:
            break
        _require_owned_directory(current, parent)
    try:
        target = path.lstat()
    except FileNotFoundError:
        return
    if path.is_symlink() or not path.is_file():
        raise InstanceConfigurationError(
            f"generated config target must be a regular file, not a symlink or directory: {path}"
        )
    if target.st_uid != os.getuid():
        raise InstanceConfigurationError(
            f"generated config is not owned by the current user: {path}"
        )
    from odoo_instance_sdk.internal.git_worktree import GitError, is_tracked_path

    try:
        if is_tracked_path(path):
            raise InstanceConfigurationError(
                "project-owned runtime config is tracked; refusing secret write: .odcli/odoo.conf"
            )
    except GitError as exc:
        raise InstanceConfigurationError(
            "unable to verify project-owned runtime config tracking; refusing secret write"
        ) from exc


def _require_owned_directory(path: Path, metadata: os.stat_result) -> None:
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise InstanceConfigurationError(
            f"generated config parent must be a project-owned directory: {path}"
        )
    if metadata.st_uid != os.getuid() or metadata.st_mode & 0o022:
        raise InstanceConfigurationError(
            f"generated config parent has unsafe ownership or permissions: {path}"
        )


def generated_config_needs_repair(project_path: Path, config: ProjectConfig) -> bool:
    """Compare generated bytes to current inputs without creating anything."""
    if config.postgres is None or config.postgres.mode != "compose":
        return False
    destination = project_generated_config_path(project_path)
    try:
        if destination.is_symlink() or not destination.is_file():
            return True
        if destination.stat().st_mode & 0o777 != 0o600:
            return True
        from odoo_instance_sdk.resources.postgres import PostgresCluster

        cluster = PostgresCluster.from_project(project_path)
        if not cluster.password_file.is_file():
            return True
        password = cluster.password_file.read_text(encoding="utf-8").strip()
        source_path = _resolve_source_config_path(project_path, config.source_config)
        if config.source_config is not None and source_path is None:
            return True
        source_start = (
            StartConfig.from_odoo_config(source_path) if source_path is not None else StartConfig()
        )
        expected = render_config(
            source_path,
            destination,
            repo_root=project_path,
            worktree=project_path,
            http_interface=source_start.http_interface,
            http_port=resolve_project_http_port(config.preferred_http_port, source_start.http_port),
            db_name=config.default_source_database or source_start.db_name or "",
            db_host=cluster.endpoint_host,
            db_port=cluster.endpoint_port,
            db_user=config.postgres.user or "odoo",
            db_password=password,
            data_dir=project_owned_data_dir(project_path.resolve()),
        )
        return destination.read_text(encoding="utf-8") != expected
    except (OSError, UnicodeError, InstanceConfigurationError, ValueError):
        return True


def register_initialized_project(project_path: Path) -> None:
    """Idempotently register a project after its valid manifest is available."""
    root, common, identity = _planned_project_identity(project_path)
    project_id = f"project_{identity}"
    from odoo_instance_sdk.internal.paths import get_catalog_path
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

    catalog = BackupCatalog(db_path=get_catalog_path())
    try:
        catalog._register_project(project_id, root, common)
    finally:
        catalog.close()


def project_env_path(project_root: Path) -> Path:
    """Return the project-owned dotenv location for test-instance secrets."""
    return Path(project_root).resolve() / ".odcli" / ".env"


def write_project_env(
    project_root: Path,
    *,
    origin_pins: str | None = None,
    master_password: str | None = None,
) -> Path:
    """Write the project-owned dotenv under 0600, preserving unmanaged lines.

    Only the ``ODCLI_TEST_INSTANCE_ORIGIN_PINS`` and ``ODCLI_TEST_MASTER_PASSWORD``
    keys are managed; existing operator lines are preserved. The file is
    created with 0600 permissions and never receives a secret from argv.
    """
    dest = project_env_path(project_root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, str] = {}
    preserved: list[str] = []
    if dest.is_file():
        for line in dest.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and "=" in stripped and not stripped.startswith("#"):
                key, _, value = stripped.partition("=")
                key = key.strip()
                if key in (_ORIGIN_PINS_KEY, _MASTER_PASSWORD_KEY):
                    existing[key] = value
                    continue
            preserved.append(line)
    lines = list(preserved)
    if lines and lines[-1] != "":
        lines.append("")
    pin_value = origin_pins if origin_pins is not None else existing.get(_ORIGIN_PINS_KEY, "")
    lines.append(f"{_ORIGIN_PINS_KEY}={pin_value}")
    master_value: str
    if master_password is not None:
        master_value = master_password
    elif _MASTER_PASSWORD_KEY in existing:
        master_value = existing[_MASTER_PASSWORD_KEY]
    else:
        master_value = ""
    lines.append(f"{_MASTER_PASSWORD_KEY}={master_value}")
    content = "\n".join(lines) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=str(dest.parent), suffix=".env.tmp", prefix=".env")
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, dest)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
    return dest


def read_project_env(project_root: Path) -> dict[str, str]:
    """Read only the managed keys from the project-owned dotenv, if present."""
    dest = project_env_path(project_root)
    if not dest.is_file():
        return {}
    values: dict[str, str] = {}
    for line in dest.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if key in (_ORIGIN_PINS_KEY, _MASTER_PASSWORD_KEY):
            values[key] = value
    return values


_ORIGIN_PINS_KEY = "ODCLI_TEST_INSTANCE_ORIGIN_PINS"
_MASTER_PASSWORD_KEY = "ODCLI_TEST_MASTER_PASSWORD"


def merge_preserved_test_instance(
    config: ProjectConfig,
    existing_test_instance: TestInstanceProjectConfig | None,
) -> ProjectConfig:
    """Preserve an existing ``[test_instance]`` when init omits test-instance flags."""
    if config.test_instance is not None or existing_test_instance is None:
        return config
    from msgspec import structs

    return structs.replace(config, test_instance=existing_test_instance)


_INIT_REMOTE_NAMES_ACTION_ID = "init.remote_database_names"


def remote_database_names_action() -> PreparedAction:
    """Return the HTTP ActionStep for init-time remote database list lookup."""
    from odoo_instance_sdk.internal.proc import PreparedAction

    return PreparedAction(
        step_id=_INIT_REMOTE_NAMES_ACTION_ID,
        action="fetch-remote-database-names",
        description="HTTP database list for init completeness",
        read_only=True,
    )


def fetch_remote_database_names_for_init(
    test_instance: TestInstanceProjectConfig,
) -> list[str] | None:
    """Resolve remote database names via ``DatabaseResource.names()`` for init.

    Failures return ``None`` so the completeness check keeps ``test_database``
    in the missing set rather than aborting ``init``.
    """
    from odoo_instance_sdk.config import InstanceConfig, OdooClientConfig
    from odoo_instance_sdk.resources.instance import OdooInstance

    @dataclass(frozen=True, slots=True)
    class _InitRemoteLookupClient:
        config: OdooClientConfig

    try:
        instance = OdooInstance(
            config=InstanceConfig(base_url=test_instance.base_url),
            _client=_InitRemoteLookupClient(config=OdooClientConfig(executable="python3")),  # type: ignore[arg-type]
        )
        return list(instance.databases.names())
    except Exception:
        return None


def evaluate_init_completeness(  # noqa: C901
    *,
    project_root: Path,
    config: ProjectConfig,
    local_config: bool,
    postgres_image: str | None,
    existing_test_instance: TestInstanceProjectConfig | None,
    dry_run: bool,
    remote_database_names: list[str] | None,
) -> tuple[list[str], dict[str, str]]:
    """Return the missing capability groups and a details map per design D5.

    ``remote_database_names`` is ``None`` on dry-run (no HTTP call) and the
    list returned by :py:meth:`DatabaseResource.names` on execute. When the
    list has exactly one name, ``test_database`` is not missing.
    """
    missing: list[str] = []
    details: dict[str, str] = {}
    test_instance = config.test_instance or existing_test_instance
    test_url = test_instance.base_url if test_instance is not None else None
    test_database = test_instance.database if test_instance is not None else None
    test_branch = test_instance.git_branch if test_instance is not None else None

    if test_url is None and not config.remote_instances:
        missing.append("test_url")
        details["test_url"] = "no --test-url and no [test_instance].url"
    if test_branch is None and not config.remote_instances:
        missing.append("test_branch")
        details["test_branch"] = "no --test-branch and no [test_instance].git_branch"
    if test_database is None and not config.remote_instances:
        if remote_database_names is not None and len(remote_database_names) == 1:
            details["test_database"] = f"resolved to {remote_database_names[0]!r}"
        else:
            missing.append("test_database")
            if dry_run or remote_database_names is None:
                details["test_database"] = "no --test-database and no [test_instance].database"
            elif len(remote_database_names) == 0:
                details["test_database"] = "remote database list is empty"
            else:
                details["test_database"] = (
                    f"remote database list has {len(remote_database_names)} names: "
                    f"{', '.join(sorted(remote_database_names))}"
                )

    if local_config:
        target = project_generated_config_path(project_root)
        if config.source_config is None or Path(config.source_config).resolve() != target:
            missing.append("local_config")
            details["local_config"] = f"--local-config requires generated {target} as source_config"
    else:
        generated = project_generated_config_path(project_root)
        if config.source_config is not None and Path(config.source_config).resolve() == generated:
            # Generated config already effective: not missing.
            pass
        elif config.source_config is None:
            missing.append("local_config")
            details["local_config"] = "no --local-config and no effective source_config selected"

    manifest_postgres_image = (
        config.postgres.image if config.postgres is not None and config.postgres.image else None
    )
    if (
        config.postgres is not None
        and config.postgres.mode == "compose"
        and postgres_image is None
        and manifest_postgres_image is None
    ):
        missing.append("postgres_mode_image")
        details["postgres_mode_image"] = "compose mode requires --postgres-image"

    # dotenv_origin: a non-loopback test URL being set requires the pin line.
    if test_url is not None:
        from urllib.parse import urlsplit

        from odoo_instance_sdk.internal.test_instance_trust import approved_test_instance_origins
        from odoo_instance_sdk.internal.urls import canonical_origin, is_loopback_host

        env_values = read_project_env(project_root)
        pins = env_values.get(_ORIGIN_PINS_KEY, "")
        approved = approved_test_instance_origins(
            environ={
                _ORIGIN_PINS_KEY: pins,
            }
        )
        try:
            origin = canonical_origin(test_url)
        except Exception:
            origin = test_url
        host = urlsplit(test_url).hostname or ""
        if not is_loopback_host(host) and origin not in approved:
            missing.append("dotenv_origin")
            details["dotenv_origin"] = (
                f"non-loopback test URL origin {origin!r} is not in .odcli/.env pins"
            )

    return missing, details
