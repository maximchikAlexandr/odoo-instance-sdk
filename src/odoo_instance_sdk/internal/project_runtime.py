"""Resolution of Python runtimes declared by a project manifest."""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from odoo_instance_sdk.exceptions import InstanceConfigurationError

_UV_PYTHON_SELECTOR = re.compile(r"^\d+\.\d+(?:\.\d+)?$")


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
