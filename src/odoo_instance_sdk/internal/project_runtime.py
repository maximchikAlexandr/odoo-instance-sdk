"""Resolution of Python runtimes declared by a project manifest."""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from odoo_instance_sdk.exceptions import EnvironmentConflictError, InstanceConfigurationError
from odoo_instance_sdk.internal.address import AddressState, probe_address
from odoo_instance_sdk.project import ProjectConfig

if TYPE_CHECKING:
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

_UV_PYTHON_SELECTOR = re.compile(r"^\d+\.\d+(?:\.\d+)?$")


def resolve_project_http_port(
    preferred_http_port: int | None,
    configured_http_port: int | None,
    assigned_http_port: int | None = None,
) -> int:
    """Apply the shared project endpoint precedence for HTTP ports."""
    if preferred_http_port is not None:
        return preferred_http_port
    if assigned_http_port is not None:
        return assigned_http_port
    return configured_http_port if configured_http_port is not None else 8069


@dataclass(frozen=True, slots=True)
class ProjectHttpPortSelection:
    """Bounded explanation for the endpoint selected for a project run."""

    project: ProjectConfig
    port: int
    selection_kind: Literal["explicit", "automatic_reused", "automatic_fallback"]
    fallback_reason: Literal["occupied", "unavailable"] | None = None


def select_project_http_port(
    project: ProjectConfig, catalog: BackupCatalog | None
) -> ProjectHttpPortSelection:
    """Select and persist a free automatic project port under its manifest lock."""
    from msgspec.structs import replace

    from odoo_instance_sdk.internal.generated_config import project_generated_config_path
    from odoo_instance_sdk.internal.odoo_config import parse_odoo_config
    from odoo_instance_sdk.internal.port_allocation import find_free_port
    from odoo_instance_sdk.internal.project_init import (
        _rewrite_project_generated_database,
        project_lifecycle_lock,
        switch_project_default,
    )

    root = project.repository_root.resolve()
    with project_lifecycle_lock(root):
        current = ProjectConfig.load(root)
        generated = project_generated_config_path(root)
        config_path = (
            generated
            if current.postgres is not None
            and current.postgres.mode == "compose"
            and generated.is_file()
            else (root / current.source_config).resolve()
            if current.source_config is not None
            else None
        )
        config = parse_odoo_config(config_path) if config_path is not None else {}
        http_interface = config.get("http_interface", "127.0.0.1")
        if current.preferred_http_port is not None:
            try:
                selected = find_free_port(
                    "http",
                    catalog,
                    exclude_project=root,
                    host=http_interface,
                    requested=current.preferred_http_port,
                    project=current,
                )
            except EnvironmentConflictError as exc:
                state = probe_address(http_interface, current.preferred_http_port)
                status = "occupied" if state is AddressState.OCCUPIED else "occupied or reserved"
                raise EnvironmentConflictError(
                    "port_in_use",
                    f"Explicit project HTTP endpoint {http_interface}:{current.preferred_http_port} "
                    f"is {status}; free the port or update/remove preferred_http_port and retry.",
                    details={
                        "host": http_interface,
                        "port": current.preferred_http_port,
                        "kind": "http",
                    },
                ) from exc
            return ProjectHttpPortSelection(current, selected, "explicit")

        configured: int | None = None
        raw = config.get("http_port")
        if raw:
            try:
                configured = int(raw)
            except ValueError:
                configured = None

        if current.assigned_http_port is not None:
            candidate = current.assigned_http_port
        elif configured is not None:
            candidate = configured
        else:
            candidate = 8069

        state = probe_address(http_interface, candidate)
        fallback_reason: Literal["occupied", "unavailable"] | None = None
        try:
            selected = find_free_port(
                "http",
                catalog,
                exclude_project=root,
                host=http_interface,
                requested=candidate,
                project=current,
            )
            selection_kind: Literal["explicit", "automatic_reused", "automatic_fallback"] = (
                "automatic_reused"
            )
            fallback_reason = None
        except EnvironmentConflictError:
            selected = find_free_port(
                "http",
                catalog,
                exclude_project=root,
                host=http_interface,
                project=current,
                reserved_ports=(candidate,),
            )
            selection_kind = "automatic_fallback"
            fallback_reason = "occupied" if state is AddressState.OCCUPIED else "unavailable"

        updated = replace(current, assigned_http_port=selected)
        has_owned_config = (
            current.postgres is not None
            and current.postgres.mode == "compose"
            and generated.is_file()
        )
        if updated != current:
            # This helper rolls the manifest back if generated-config repair fails.
            switch_project_default(root, updated)
        elif has_owned_config and config.get("http_port") != str(selected):
            _rewrite_project_generated_database(root, current, generated)
        return ProjectHttpPortSelection(updated, selected, selection_kind, fallback_reason)


@dataclass(frozen=True, slots=True)
class DeferredProjectRuntime:
    """A project Python selector represented by one native ``uv run`` prefix."""

    repository_root: Path
    selector: str
    uv_executable: Path
    odoo_bin: Path
    field: str = "python"

    def command_prefix(self) -> tuple[str, ...]:
        """Return the immutable native prefix used for every Odoo operation."""
        return uv_run_prefix(
            self.selector,
            uv_executable=self.uv_executable,
            command=("python", str(self.odoo_bin)),
        )


def resolve_project_runtime(
    repository_root: str | Path,
    value: str | Path | None,
    *,
    field: str = "python",
    uv_executable: str | Path | None = None,
) -> Path:
    """Resolve a project runtime path or executable selector.

    ``Path`` values and path-shaped strings are resolved relative to the
    repository root and must point to a file. Bare string values are
    executable selectors and are resolved through ``PATH`` with
    :func:`shutil.which`.
    """
    if value is None:
        raise InstanceConfigurationError(f"Project manifest requires {field}")

    root = Path(repository_root)
    if isinstance(value, Path) or _is_path_value(str(value)):
        path = Path(value)
        if not path.is_absolute():
            path = root / path
        path = Path(os.path.abspath(path))
        return _validated_path(path, base=root, field=field, value=value)

    selector = str(value)
    if _UV_PYTHON_SELECTOR.fullmatch(selector):
        return _resolve_uv_python(root, selector, field=field, uv_executable=uv_executable)

    selected = shutil.which(selector)
    if selected is None:
        raise InstanceConfigurationError(f"Project {field} executable not found on PATH: {value}")
    return _validated_path(selected, base=Path.cwd(), field=field, value=value)


def defer_project_runtime(
    repository_root: str | Path,
    value: str | Path | None,
    *,
    odoo_bin: str | Path,
    field: str = "python",
) -> DeferredProjectRuntime | None:
    """Capture a version selector without launching its resolver process."""
    if value is None:
        raise InstanceConfigurationError(f"Project manifest requires {field}")
    selector = str(value)
    if not _UV_PYTHON_SELECTOR.fullmatch(selector):
        return None
    uv = resolve_uv_executable()
    root = Path(repository_root).resolve()
    return DeferredProjectRuntime(
        repository_root=root,
        selector=selector,
        uv_executable=uv,
        odoo_bin=Path(odoo_bin),
        field=field,
    )


def is_uv_python_selector(value: str | Path | None) -> bool:
    """Return whether a manifest value is a uv-managed Python version selector."""
    return isinstance(value, str) and _UV_PYTHON_SELECTOR.fullmatch(value) is not None


def _resolve_uv_python(
    root: Path,
    selector: str,
    *,
    field: str,
    uv_executable: str | Path | None = None,
) -> Path:
    from odoo_instance_sdk.internal.server import run_command

    uv = resolve_uv_executable(uv_executable, field=field, selector=selector)
    result = run_command(
        str(uv),
        ["python", "find", selector],
        cwd=root,
        timeout=30.0,
    )
    if result.returncode != 0:
        raise InstanceConfigurationError(
            f"Project {field} selector could not be resolved by uv: {selector}"
        )
    selected = result.stdout.strip()
    if not selected:
        raise InstanceConfigurationError(
            f"Project {field} selector returned no interpreter: {selector}"
        )
    return _validated_path(selected, base=root, field=field, value=selector)


def resolve_uv_executable(
    value: str | Path | None = None,
    *,
    field: str = "python",
    selector: str | None = None,
) -> Path:
    """Resolve the ``uv`` binary once without starting a child process."""
    raw = "uv" if value is None else str(value)
    if isinstance(value, Path) or _is_path_value(raw):
        candidate = _validated_path(value or raw, base=Path.cwd(), field="uv", value=raw)
    else:
        selected = shutil.which(raw)
        if selected is None:
            suffix = f": {selector}" if selector is not None else ""
            raise InstanceConfigurationError(
                f"Project {field} selector requires uv on PATH{suffix}"
            )
        candidate = _validated_path(selected, base=Path.cwd(), field="uv", value=raw)
    return candidate


def uv_run_prefix(
    selector: str,
    *,
    uv_executable: str | Path | None = None,
    command: Sequence[str] = (),
) -> tuple[str, ...]:
    """Build the exact native argv prefix for a selector-backed operation."""
    uv = resolve_uv_executable(uv_executable, selector=selector)
    return (str(uv), "run", "--no-project", "--python", selector, "--", *command)


def _validated_path(
    selected: str | Path,
    *,
    base: Path,
    field: str,
    value: str | Path,
) -> Path:
    path = Path(selected)
    if not path.is_absolute():
        path = base / path
    path = Path(os.path.abspath(path))
    if not path.is_file() or not os.access(path, os.X_OK):
        raise InstanceConfigurationError(
            f"Project {field} executable not found or not executable: {value}"
        )
    return path


def _is_path_value(value: str) -> bool:
    """Distinguish repository-relative paths from bare executable selectors."""
    return value.startswith((".", "/")) or "/" in value or "\\" in value


__all__ = [
    "DeferredProjectRuntime",
    "defer_project_runtime",
    "is_uv_python_selector",
    "resolve_project_runtime",
    "resolve_uv_executable",
    "uv_run_prefix",
]
