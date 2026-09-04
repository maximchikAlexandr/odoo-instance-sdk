"""Conservative, project-bound dotenv loading.

This is deliberately not a shell or ``python-dotenv`` parser.  Project
configuration is data, and only the small grammar documented by the CLI is
accepted here.
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Final

from odoo_instance_sdk.exceptions import ConfigError

PROJECT_ENV_FILENAME: Final = ".env"
PROJECT_ENV_DIRECTORY: Final = ".odcli"
MASTER_PASSWORD_KEY: Final = "ODCLI_TEST_MASTER_PASSWORD"
_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_SECRET_KEY = re.compile(
    r"(?:^|[-_])(?:password|passwd|pwd|secret|token|cookie|jwt|oauth|api[-_]?key|"
    r"dsn|authorization|bearer|credential|private[-_]?key|access[-_]?key|auth)(?:$|[-_])",
    re.IGNORECASE,
)
_HORIZONTAL = " \t"
_DOUBLE_ESCAPES: Final = {
    "\\": "\\",
    '"': '"',
    "n": "\n",
    "r": "\r",
    "t": "\t",
}


class ProjectEnvironmentError(ConfigError):
    """A sanitized project dotenv error containing no key or value."""


def project_environment_path(project_root: str | Path) -> Path:
    """Return the only dotenv path accepted for ``project_root``.

    Resolving the target as well as the root rejects a symlinked ``.odcli`` or
    file which escapes the project boundary.  The returned path remains the
    project-relative path so diagnostics are stable and actionable.
    """
    root = Path(project_root).resolve()
    path = root / PROJECT_ENV_DIRECTORY / PROJECT_ENV_FILENAME
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ProjectEnvironmentError(
            f"{path}: project dotenv path must remain inside the resolved project"
        ) from exc
    return path


def load_project_environment(project_root: str | Path) -> Mapping[str, str]:
    """Load the project-local file into an immutable file-values mapping.

    A missing file is intentionally represented by an empty immutable mapping.
    Ambient process values are merged only at a child boundary by
    :func:`effective_project_environment`; this keeps file values distinct so
    denied child classes cannot accidentally inherit them.
    """
    path = project_environment_path(project_root)
    if not path.exists():
        return MappingProxyType({})
    try:
        metadata = path.stat()
    except OSError as exc:
        raise ProjectEnvironmentError(f"{path}: unable to read project dotenv file") from exc
    mode = stat.S_IMODE(metadata.st_mode)
    if os.name == "posix" and (mode & 0o077 or not mode & stat.S_IRUSR):
        raise ProjectEnvironmentError(
            f"{path}: project dotenv file requires owner-only permissions"
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ProjectEnvironmentError(f"{path}: unable to read project dotenv file") from exc
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ProjectEnvironmentError(f"{path}: invalid UTF-8 in project dotenv file") from exc
    if "\x00" in text:
        raise ProjectEnvironmentError(f"{path}:1: NUL is not allowed in project dotenv file")
    return MappingProxyType(_parse(text, path))


def effective_project_environment(
    file_values: Mapping[str, str],
    process_environment: Mapping[str, str] | None = None,
) -> Mapping[str, str]:
    """Return an immutable process-precedence environment snapshot.

    The process mapping is copied, never modified, and wins for every key.  A
    caller can pass the result to an Odoo-only child boundary; generic child
    builders should continue using their existing purpose-built environment.
    """
    effective = dict(os.environ if process_environment is None else process_environment)
    for key, value in file_values.items():
        effective.setdefault(key, value)
    return MappingProxyType(effective)


def project_environment_secret_values(file_values: Mapping[str, str]) -> tuple[str, ...]:
    """Return values that must be scrubbed if a child echoes its inputs."""
    return tuple(
        value
        for key, value in file_values.items()
        if value and (key == MASTER_PASSWORD_KEY or _SECRET_KEY.search(key))
    )


def _parse(text: str, path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(_physical_lines(text), start=1):
        line = raw_line
        if "\r" in line:
            _malformed(path, line_number)
        stripped = line.lstrip(_HORIZONTAL)
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or _KEY.fullmatch(key.strip(_HORIZONTAL)) is None:
            _malformed(path, line_number)
        normalized_key = key.strip(_HORIZONTAL)
        if normalized_key in values:
            raise ProjectEnvironmentError(f"{path}:{line_number}: duplicate project dotenv key")
        value = value.strip(_HORIZONTAL)
        parsed = _parse_value(value, path, line_number)
        values[normalized_key] = parsed
    return values


def _physical_lines(text: str) -> list[str]:
    """Split only LF/CRLF physical lines."""
    lines: list[str] = []
    for line in text.split("\n"):
        if line.endswith("\r"):
            lines.append(line[:-1])
        else:
            lines.append(line)
    return lines


def _parse_value(value: str, path: Path, line_number: int) -> str:
    if any(marker in value for marker in ("$", "`")):
        _malformed(path, line_number)
    if not value:
        return ""
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'"):
            _malformed(path, line_number)
        content = value[1:-1]
        if any(char in content for char in ("'", "\\", "\r", "\n", "\x00")):
            _malformed(path, line_number)
        return content
    if value.startswith('"'):
        return _parse_double_quoted(value, path, line_number)
    if any(char in value for char in ("'", '"')):
        _malformed(path, line_number)
    return value


def _parse_double_quoted(value: str, path: Path, line_number: int) -> str:
    result: list[str] = []
    index = 1
    while index < len(value):
        char = value[index]
        if char == '"':
            if index != len(value) - 1:
                _malformed(path, line_number)
            return "".join(result)
        if char in ("\r", "\n", "\x00"):
            _malformed(path, line_number)
        if char != "\\":
            result.append(char)
            index += 1
            continue
        index += 1
        if index >= len(value) or value[index] not in _DOUBLE_ESCAPES:
            _malformed(path, line_number)
        result.append(_DOUBLE_ESCAPES[value[index]])
        index += 1
    _malformed(path, line_number)
    return ""


def _malformed(path: Path, line_number: int) -> None:
    raise ProjectEnvironmentError(f"{path}:{line_number}: malformed project dotenv assignment")


__all__ = [
    "MASTER_PASSWORD_KEY",
    "ProjectEnvironmentError",
    "effective_project_environment",
    "load_project_environment",
    "project_environment_path",
    "project_environment_secret_values",
]
