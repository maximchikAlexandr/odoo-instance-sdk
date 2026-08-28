from __future__ import annotations

import contextlib
import keyword
import os
import selectors
import subprocess
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO, TYPE_CHECKING, Literal, NoReturn, cast

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.internal.postgres_transport import run_psql
from odoo_instance_sdk.internal.process_env import sanitized_child_environment
from odoo_instance_sdk.internal.sanitize import sanitize_last_error
from odoo_instance_sdk.models import StartConfig

if TYPE_CHECKING:
    from odoo_instance_sdk.resources.instance import OdooInstance

SelectionKind = Literal["module", "cwd", "file"]


@dataclass(frozen=True, slots=True)
class _RootDiagnostic:
    configured: str
    reason: str


@dataclass(frozen=True, slots=True)
class _EligibleRoots:
    worktree: Path
    roots: tuple[Path, ...]
    rejected: tuple[_RootDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class _SelectorProvenance:
    kind: SelectionKind
    value: str | None
    module_path: Path
    file_path: Path | None = None


@dataclass(frozen=True, slots=True)
class _TestSelection:
    """Private, typed selection plan consumed by the future CLI adapter."""

    provenance: _SelectorProvenance
    modules: tuple[str, ...]
    test_tags: str
    eligible_roots: tuple[Path, ...]
    rejected_roots: tuple[_RootDiagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class _ChangedSelection:
    """Private changed-test plan and its complete local Git provenance."""

    base_source: Literal["explicit", "environment"]
    requested_base: str
    resolved_base: str | None
    merge_base: str | None
    head: str | None
    changed_files: tuple[str, ...]
    modules: tuple[str, ...]
    ignored_paths: tuple[str, ...]
    unmapped_paths: tuple[str, ...]
    test_tags: str | None

    @property
    def executable(self) -> bool:
        return bool(self.modules) and not self.unmapped_paths


class _ChangedSelectionError(ConfigError):
    """A changed-selection failure with the Git provenance resolved so far."""

    def __init__(self, message: str, plan: _ChangedSelection) -> None:
        self.plan = plan
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class _PreflightResult:
    database: str
    installed_modules: tuple[str, ...]


def _contained(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _valid_module_name(value: str) -> bool:
    return bool(value) and value.isidentifier() and not keyword.iskeyword(value)


def _has_symlink_component(path: Path) -> bool:
    """Return whether an existing component of an absolute path is a symlink."""
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        if current.is_symlink():
            return True
    return False


def _root_values(
    addons_path: Iterable[str] | StartConfig | None,
) -> tuple[str, ...]:
    values: Iterable[str]
    if isinstance(addons_path, StartConfig):
        values = addons_path.addons_path or ()
    else:
        values = addons_path or ()
    return tuple(str(value) for value in values)


def _normalize_root_set(
    worktree_root: str | Path,
    addons_path: Iterable[str] | StartConfig | None,
) -> _EligibleRoots:
    worktree = Path(worktree_root).resolve()
    if not worktree.is_dir() or worktree.is_symlink():
        raise ConfigError(f"registered worktree is not a safe directory: {worktree}")

    roots: set[Path] = set()
    rejected: list[_RootDiagnostic] = []
    for raw in _root_values(addons_path):
        configured = raw
        lexical = Path(raw)
        if not lexical.is_absolute():
            lexical = worktree / lexical
        if lexical.is_symlink() or _has_symlink_component(lexical):
            rejected.append(_RootDiagnostic(configured, "contains a symlink"))
            continue
        try:
            canonical = lexical.resolve(strict=True)
        except OSError:
            rejected.append(_RootDiagnostic(configured, "does not exist"))
            continue
        if not canonical.is_dir():
            rejected.append(_RootDiagnostic(configured, "is not a directory"))
            continue
        if not _contained(canonical, worktree):
            rejected.append(_RootDiagnostic(configured, "is outside the registered worktree"))
            continue
        roots.add(canonical)

    return _EligibleRoots(
        worktree=worktree,
        roots=tuple(sorted(roots, key=str)),
        rejected=tuple(sorted(rejected, key=lambda item: (item.configured, item.reason))),
    )


def normalize_eligible_roots(
    worktree_root: str | Path,
    addons_path: Iterable[str] | StartConfig | None = None,
    *,
    start_config: StartConfig | None = None,
) -> tuple[Path, ...]:
    """Return only canonical, existing, worktree-contained addon roots."""
    if start_config is not None:
        if addons_path is not None:
            raise ConfigError("pass either addons_path or start_config, not both")
        addons_path = start_config
    return _normalize_root_set(worktree_root, addons_path).roots


def _safe_manifest(module_path: Path, *, root: Path, worktree: Path) -> bool:
    manifest = module_path / "__manifest__.py"
    return (
        module_path != root
        and module_path.is_dir()
        and not module_path.is_symlink()
        and _contained(module_path.resolve(), root)
        and _contained(module_path.resolve(), worktree)
        and manifest.is_file()
        and not manifest.is_symlink()
    )


def _module_candidates(
    module: str,
    roots: Sequence[Path],
    worktree: Path,
) -> tuple[Path, ...]:
    if not _valid_module_name(module):
        raise ConfigError(f"invalid addon module name: {module!r}")
    candidates = [
        root / module
        for root in roots
        if _safe_manifest(root / module, root=root, worktree=worktree)
    ]
    return tuple(sorted(candidates, key=str))


def _format_candidates(candidates: Iterable[Path]) -> str:
    values = tuple(sorted({str(path) for path in candidates}))
    return ", ".join(values) if values else "none"


def _resolve_bare_module(module: str, roots: _EligibleRoots) -> Path:
    candidates = _module_candidates(module, roots.roots, roots.worktree)
    if not candidates:
        raise ConfigError(
            f"addon module {module!r} was not found in safe configured roots "
            f"({_format_candidates(roots.roots)})"
        )
    if len(candidates) != 1:
        raise ConfigError(
            f"addon module {module!r} is ambiguous; safe candidates: "
            f"{_format_candidates(candidates)}"
        )
    return candidates[0]


def _nearest_module_manifest(path: Path, roots: Sequence[Path], worktree: Path) -> Path | None:
    for root in roots:
        if not _contained(path, root):
            continue
        current = path
        while _contained(current, root):
            if _safe_manifest(current, root=root, worktree=worktree):
                return current
            if current == root:
                break
            current = current.parent
    return None


def _nearest_unique_module_manifest(path: Path, roots: Sequence[Path], worktree: Path) -> Path:
    candidates: list[Path] = []
    for root in roots:
        candidate = _nearest_module_manifest(path, (root,), worktree)
        if candidate is not None:
            candidates.append(candidate)
    unique = tuple(sorted(set(candidates), key=str))
    if not unique:
        raise ConfigError(
            f"path {path} is not inside a safe configured addon module "
            f"({_format_candidates(roots)})"
        )
    if len(unique) > 1:
        raise ConfigError(
            f"path {path} has ambiguous addon candidates: {_format_candidates(unique)}"
        )
    return unique[0]


def _resolve_cwd(cwd: str | Path | None) -> Path:
    raw = Path.cwd() if cwd is None else Path(cwd)
    if raw.is_symlink() or _has_symlink_component(raw):
        raise ConfigError("current directory must not use a symlink")
    return raw.resolve()


def _resolve_file_target(target: str, cwd: Path, roots: _EligibleRoots) -> tuple[Path, Path]:
    raw = Path(target)
    candidate = raw if raw.is_absolute() else cwd / raw
    if _has_symlink_component(candidate) or candidate.is_symlink():
        raise ConfigError(f"test file must not use a symlink: {target!r}")
    path = candidate.resolve()
    if not _contained(path, roots.worktree):
        raise ConfigError(f"test file is outside the registered worktree: {target!r}")
    if not path.is_file() or path.is_symlink():
        raise ConfigError(f"test target is not a regular file: {target!r}")
    if path.name == "__init__.py" or not path.name.startswith("test_") or path.suffix != ".py":
        raise ConfigError("test file must be a regular Python file named test_*.py")

    module_path = _nearest_unique_module_manifest(path.parent, roots.roots, roots.worktree)
    tests_path = module_path / "tests"
    if (
        tests_path.is_symlink()
        or not tests_path.is_dir()
        or not _contained(path, tests_path.resolve())
        or _has_symlink_component(path)
    ):
        raise ConfigError("test file must be beneath the module's literal tests/ directory")
    return module_path, path


def _resolve_target(
    target: str | None, cwd: Path, roots: _EligibleRoots
) -> tuple[Path, Path | None, SelectionKind, str | None]:
    if target is None:
        return (
            _nearest_unique_module_manifest(cwd, roots.roots, roots.worktree),
            None,
            "cwd",
            None,
        )
    if _valid_module_name(target):
        return _resolve_bare_module(target, roots), None, "module", target
    module_path, file_path = _resolve_file_target(target, cwd, roots)
    return module_path, file_path, "file", target


def resolve_test_selection(
    worktree_root: str | Path,
    addons_path: Iterable[str] | StartConfig | None = None,
    *,
    target: str | None = None,
    cwd: str | Path | None = None,
    tags: str | None = None,
    start_config: StartConfig | None = None,
) -> _TestSelection:
    """Resolve one module, current-directory, or test-file operation safely.

    This module deliberately has no Click or renderer dependency.  Explicit tags
    are retained byte-for-byte; only their blankness is validated here.
    """
    if target is not None and not isinstance(target, str):
        raise ConfigError("test target must be a string")
    if tags is not None and not tags.strip():
        raise ConfigError("test tags must not be blank")

    if start_config is not None:
        if addons_path is not None:
            raise ConfigError("pass either addons_path or start_config, not both")
        addons_path = start_config
    roots = _normalize_root_set(worktree_root, addons_path)
    selected_cwd = _resolve_cwd(cwd)
    if not _contained(selected_cwd, roots.worktree):
        raise ConfigError(f"current directory {selected_cwd} is outside the registered worktree")

    module_path, file_path, kind, value = _resolve_target(target, selected_cwd, roots)

    module = module_path.name
    if tags is None:
        if file_path is None:
            native_tags = f"/{module}"
        else:
            relative = file_path.relative_to(module_path).as_posix()
            native_tags = f"/{module}/{relative}"
    else:
        if file_path is not None:
            raise ConfigError("--tags cannot be combined with an explicit test file")
        native_tags = tags

    return _TestSelection(
        provenance=_SelectorProvenance(
            kind=kind,
            value=value,
            module_path=module_path,
            file_path=file_path,
        ),
        modules=(module,),
        test_tags=native_tags,
        eligible_roots=roots.roots,
        rejected_roots=roots.rejected,
    )


def preflight_installed_modules(
    instance: OdooInstance,
    modules: Sequence[str],
    *,
    timeout: float = 10.0,
) -> _PreflightResult:
    """Check one bound database and selected installed modules without Odoo."""
    selected = tuple(modules)
    if not selected:
        raise ConfigError("test preflight requires at least one module")
    if any(not _valid_module_name(module) for module in selected):
        raise ConfigError("test preflight received an invalid module name")
    if tuple(sorted(set(selected))) != selected:
        raise ConfigError("test preflight modules must be sorted and unique")

    config = instance.config
    database_names = tuple(config.configured_database_names)
    if len(database_names) != 1:
        raise ConfigError(
            "test preflight requires exactly one configured database name "
            f"(found {len(database_names)})"
        )
    database = database_names[0]
    escaped = (module.replace("'", "''") for module in selected)
    literals = ", ".join(f"'{module}'" for module in escaped)
    query = (
        "SELECT name FROM ir_module_module "
        "WHERE state = 'installed' AND name IN ("
        f"{literals}) ORDER BY name"
    )
    result = run_psql(
        host=config.db_host,
        port=config.db_port or 5432,
        user=config.db_user,
        password=config.db_password,
        query=query,
        timeout=timeout,
        database=database,
    )
    if result is None:
        raise ConfigError("test preflight could not run bounded PostgreSQL query")
    if result.returncode != 0:
        diagnostic = sanitize_last_error(result.stderr) or "no diagnostic"
        raise ConfigError(f"test preflight database query failed: {diagnostic}")

    installed = tuple(sorted({line.strip() for line in result.stdout.splitlines() if line.strip()}))
    missing = tuple(module for module in selected if module not in installed)
    if missing:
        raise ConfigError("selected modules are not installed: " + ", ".join(missing))
    return _PreflightResult(database=database, installed_modules=installed)


_GIT_TIMEOUT_SECONDS = 10.0
_GIT_OUTPUT_LIMIT = 4 * 1024 * 1024


def _run_git_bytes(  # noqa: C901
    argv: Sequence[str],
    cwd: Path,
    *,
    timeout: float = _GIT_TIMEOUT_SECONDS,
    max_output_bytes: int = _GIT_OUTPUT_LIMIT,
) -> subprocess.CompletedProcess[bytes]:
    """Run one bounded, local Git argv without decoding or shell interpolation."""
    if max_output_bytes < 0:
        raise ValueError("max_output_bytes must not be negative")
    command = ["git", "-C", str(cwd), *argv]
    try:
        process = subprocess.Popen(
            command,
            env=sanitized_child_environment(),
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise ConfigError("git is not available for changed test selection") from exc
    except OSError as exc:
        raise ConfigError("git could not be started for changed test selection") from exc

    stdout = process.stdout
    stderr = process.stderr
    assert stdout is not None
    assert stderr is not None
    streams: dict[IO[bytes], bytearray] = {stdout: bytearray(), stderr: bytearray()}
    selector = selectors.DefaultSelector()
    started = time.monotonic()

    def terminate_and_reap() -> None:
        if process.poll() is None:
            with contextlib.suppress(OSError):
                process.terminate()
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(OSError):
                process.kill()
            process.wait()

    def terminate_and_fail(message: str) -> NoReturn:
        terminate_and_reap()
        raise ConfigError(message)

    try:
        selector.register(stdout, selectors.EVENT_READ)
        selector.register(stderr, selectors.EVENT_READ)
        while selector.get_map():
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                terminate_and_fail("git timed out during changed test selection")
            ready = selector.select(remaining)
            if not ready:
                terminate_and_fail("git timed out during changed test selection")
            for key, _ in ready:
                stream = cast("IO[bytes]", key.fileobj)
                chunk = os.read(stream.fileno(), 64 * 1024)
                if not chunk:
                    selector.unregister(stream)
                    stream.close()
                    continue
                buffer = streams[stream]
                if len(buffer) + len(chunk) > max_output_bytes:
                    terminate_and_fail("git output exceeded the changed test selection limit")
                buffer.extend(chunk)
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            terminate_and_fail("git timed out during changed test selection")
        process.wait(timeout=remaining)
    except subprocess.TimeoutExpired:
        terminate_and_reap()
        raise ConfigError("git timed out during changed test selection") from None
    except (ConfigError, OSError):
        if process.poll() is None:
            terminate_and_reap()
        raise
    finally:
        selector.close()
        for stream in (stdout, stderr):
            if not stream.closed:
                stream.close()

    return subprocess.CompletedProcess(
        command,
        process.returncode,
        bytes(streams[stdout]),
        bytes(streams[stderr]),
    )


def _git_error(result: subprocess.CompletedProcess[bytes], argv: Sequence[str]) -> ConfigError:
    diagnostic = sanitize_last_error(os.fsdecode(result.stderr)) or "no diagnostic"
    return ConfigError(f"git {' '.join(argv)} failed (rc={result.returncode}): {diagnostic}")


def _git_checked(
    argv: Sequence[str],
    cwd: Path,
    *,
    timeout: float,
    max_output_bytes: int,
) -> bytes:
    result = _run_git_bytes(
        argv,
        cwd,
        timeout=timeout,
        max_output_bytes=max_output_bytes,
    )
    if result.returncode != 0:
        raise _git_error(result, argv)
    return result.stdout


def _git_revision(
    ref: str,
    worktree: Path,
    *,
    timeout: float,
    max_output_bytes: int,
) -> str:
    raw = _git_checked(
        ("rev-parse", "--verify", "--end-of-options", ref),
        worktree,
        timeout=timeout,
        max_output_bytes=max_output_bytes,
    )
    revision = os.fsdecode(raw).strip()
    if not revision or "\n" in revision:
        raise ConfigError(f"git returned an invalid revision for {ref!r}")
    return revision


def _selected_base(
    base: str | None, environment_base: str | None
) -> tuple[Literal["explicit", "environment"], str, str]:
    if base is not None:
        requested = base
        ref = base.strip()
        source: Literal["explicit", "environment"] = "explicit"
    else:
        requested = environment_base or ""
        ref = requested.strip()
        source = "environment"
    if not ref or ref == "HEAD":
        raise ConfigError(
            "changed test selection requires --base REF (environment base is unavailable)"
        )
    return source, requested, ref


def _decode_git_paths(payload: bytes) -> tuple[str, ...]:
    paths: set[str] = set()
    for raw in payload.split(b"\0"):
        if not raw:
            continue
        paths.add(os.fsdecode(raw))
    return tuple(sorted(paths))


def _safe_git_relative(path: str) -> tuple[str, ...] | None:
    if not path or path.startswith("/"):
        return None
    parts = tuple(PurePosixPath(path).parts)
    if not parts or any(part in ("", ".", "..") for part in parts):
        return None
    return parts


def _map_changed_path(
    path: str,
    *,
    worktree: Path,
    roots: _EligibleRoots,
) -> tuple[Literal["ignored", "unmapped", "module"], str]:
    parts = _safe_git_relative(path)
    if parts is None:
        return "unmapped", path
    candidate = worktree.joinpath(*parts)
    containing_roots = tuple(root for root in roots.roots if _contained(candidate, root))
    if not containing_roots:
        return "ignored", path
    if _has_symlink_component(candidate):
        return "unmapped", path
    try:
        module_path = _nearest_unique_module_manifest(candidate, containing_roots, worktree)
    except ConfigError:
        return "unmapped", path
    return "module", module_path.name


def _changed_plan(
    roots: _EligibleRoots,
    *,
    base_source: Literal["explicit", "environment"],
    requested_base: str,
    resolved_base: str | None = None,
    merge_base: str | None = None,
    head: str | None = None,
    changed_files: Iterable[str] = (),
    tags: str | None = None,
) -> _ChangedSelection:
    changed = tuple(sorted(set(changed_files)))
    ignored: list[str] = []
    unmapped: list[str] = []
    modules: set[str] = set()
    for path in changed:
        kind, value = _map_changed_path(path, worktree=roots.worktree, roots=roots)
        if kind == "ignored":
            ignored.append(value)
        elif kind == "unmapped":
            unmapped.append(value)
        else:
            modules.add(value)
    selected_modules = tuple(sorted(modules))
    native_tags = (
        tags
        if tags is not None
        else (",".join(f"/{module}" for module in selected_modules) if selected_modules else None)
    )
    return _ChangedSelection(
        base_source=base_source,
        requested_base=requested_base,
        resolved_base=resolved_base,
        merge_base=merge_base,
        head=head,
        changed_files=changed,
        modules=selected_modules,
        ignored_paths=tuple(ignored),
        unmapped_paths=tuple(unmapped),
        test_tags=native_tags,
    )


def _raise_changed_error(
    error: ConfigError,
    roots: _EligibleRoots,
    *,
    base_source: Literal["explicit", "environment"],
    requested_base: str,
    resolved_base: str | None = None,
    merge_base: str | None = None,
    head: str | None = None,
    changed_files: Iterable[str] = (),
    tags: str | None = None,
) -> NoReturn:
    message = sanitize_last_error(str(error)) or "changed test selection failed"
    raise _ChangedSelectionError(
        message,
        _changed_plan(
            roots,
            base_source=base_source,
            requested_base=requested_base,
            resolved_base=resolved_base,
            merge_base=merge_base,
            head=head,
            changed_files=changed_files,
            tags=tags,
        ),
    ) from error


def resolve_changed_selection(
    worktree_root: str | Path,
    addons_path: Iterable[str] | StartConfig | None = None,
    *,
    base: str | None = None,
    environment_base: str | None = None,
    tags: str | None = None,
    start_config: StartConfig | None = None,
    timeout: float = _GIT_TIMEOUT_SECONDS,
    max_output_bytes: int = _GIT_OUTPUT_LIMIT,
) -> _ChangedSelection:
    """Collect and safely map all four local Git change states."""
    if tags is not None and not tags.strip():
        raise ConfigError("test tags must not be blank")
    if start_config is not None:
        if addons_path is not None:
            raise ConfigError("pass either addons_path or start_config, not both")
        addons_path = start_config

    roots = _normalize_root_set(worktree_root, addons_path)
    source: Literal["explicit", "environment"] = "explicit" if base is not None else "environment"
    requested_base = base if base is not None else environment_base or ""
    resolved_base: str | None = None
    merge_base: str | None = None
    head: str | None = None
    changed: set[str] = set()
    try:
        source, requested_base, base_ref = _selected_base(base, environment_base)
        resolved_base_value = _git_revision(
            base_ref, roots.worktree, timeout=timeout, max_output_bytes=max_output_bytes
        )
        resolved_base = resolved_base_value
        head_value = _git_revision(
            "HEAD", roots.worktree, timeout=timeout, max_output_bytes=max_output_bytes
        )
        head = head_value
        merge_base_value = os.fsdecode(
            _git_checked(
                ("merge-base", resolved_base_value, head_value),
                roots.worktree,
                timeout=timeout,
                max_output_bytes=max_output_bytes,
            )
        ).strip()
        if not merge_base_value:
            raise ConfigError(  # noqa: TRY301
                "git could not compute a merge-base for changed test selection"
            )
        merge_base = merge_base_value

        queries = (
            ("diff", "--no-renames", "--name-only", "-z", merge_base_value, head_value),
            ("diff", "--no-renames", "--name-only", "-z", "--cached", head_value),
            ("diff", "--no-renames", "--name-only", "-z"),
            ("ls-files", "--others", "--exclude-standard", "-z"),
        )
        for query in queries:
            changed.update(
                _decode_git_paths(
                    _git_checked(
                        query,
                        roots.worktree,
                        timeout=timeout,
                        max_output_bytes=max_output_bytes,
                    )
                )
            )

        final_head = _git_revision(
            "HEAD", roots.worktree, timeout=timeout, max_output_bytes=max_output_bytes
        )
        if final_head != head_value:
            raise ConfigError(  # noqa: TRY301
                "HEAD changed during changed test selection; retry the operation"
            )
    except ConfigError as error:
        _raise_changed_error(
            error,
            roots,
            base_source=source,
            requested_base=requested_base,
            resolved_base=resolved_base,
            merge_base=merge_base,
            head=head,
            changed_files=changed,
            tags=tags,
        )

    return _changed_plan(
        roots,
        base_source=source,
        requested_base=requested_base,
        resolved_base=resolved_base,
        merge_base=merge_base,
        head=head,
        changed_files=changed,
        tags=tags,
    )
