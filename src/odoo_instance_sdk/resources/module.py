"""Safe, on-demand discovery and update planning for Odoo addons."""

from __future__ import annotations

import ast
import keyword
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

import msgspec

from odoo_instance_sdk.exceptions import (
    ConfigError,
    ModuleOperationInProgressError,
    StalePlanError,
)
from odoo_instance_sdk.internal.sanitize import sanitize_last_error
from odoo_instance_sdk.internal.test_selection import (
    _contained,
    _has_symlink_component,
    resolve_changed_selection,
)
from odoo_instance_sdk.models import (
    CommandResult,
    Module,
    ModuleAvailability,
    ModuleContext,
    ModuleDependencies,
    ModuleDependency,
    ModuleGitChange,
    ModuleInstallOrder,
    ModuleJsonValue,
    ModuleProvenance,
    ModuleUpdatePlan,
    ModuleUpdateResult,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.commands.context import RuntimeView
    from odoo_instance_sdk.execution import Command, SemanticPlanObservation
    from odoo_instance_sdk.internal.proc import PreparedStep, ProcessExecutor, RunContext
    from odoo_instance_sdk.resources.instance import OdooInstance


type _LiteralValue = (
    bool
    | int
    | float
    | str
    | list["_LiteralValue"]
    | tuple["_LiteralValue", ...]
    | dict[str, "_LiteralValue"]
    | None
)


def _json_value(value: _LiteralValue) -> ModuleJsonValue:
    """Normalize literal manifest values without evaluating any code."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            return str(value)
        return {key: _json_value(item) for key, item in value.items()}
    # Odoo manifests occasionally contain an otherwise harmless literal type
    # that is not part of the public JSON model. Keeping its text is safer
    # than executing or dropping the complete manifest.
    return str(value)


def _valid_name(name: str) -> bool:
    return bool(name) and name.isidentifier() and not keyword.iskeyword(name)


def _safe_root(raw: str, *, worktree: Path) -> Path | None:
    configured = Path(raw)
    path = configured if configured.is_absolute() else worktree / configured
    if path.is_symlink() or _has_symlink_component(path):
        return None
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return None
    if not resolved.is_dir() or not _contained(resolved, worktree):
        return None
    return resolved


def _manifest(path: Path) -> dict[str, ModuleJsonValue]:
    manifest_path = path / "__manifest__.py"
    try:
        raw = ast.literal_eval(manifest_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError, MemoryError) as exc:
        raise ConfigError(f"cannot safely parse manifest {manifest_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"manifest {manifest_path} must contain a dictionary literal")
    result = _json_value(raw)
    if not isinstance(result, dict):  # pragma: no cover - guarded by raw's type
        raise ConfigError(f"manifest {manifest_path} must contain a dictionary literal")
    return result


def _depends(manifest: Mapping[str, ModuleJsonValue], path: Path) -> tuple[str, ...]:
    raw = manifest.get("depends", ())
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)) or any(not isinstance(item, str) for item in raw):
        raise ConfigError(f"manifest {path / '__manifest__.py'} has invalid depends")
    names = tuple(cast("str", item) for item in raw)
    if any(not _valid_name(item) for item in names):
        raise ConfigError(f"manifest {path / '__manifest__.py'} has invalid dependency name")
    return names


def _instance_worktree(instance: OdooInstance) -> Path:
    cwd = instance.config.default_cwd
    if cwd is None and instance.config.start_config is not None:
        config_path = instance.config.start_config.config_path
        if config_path is not None:
            cwd = Path(config_path).parent
    if cwd is None:
        cwd = Path.cwd()
    try:
        root = Path(cwd).resolve(strict=True)
    except OSError as exc:
        raise ConfigError(f"runtime worktree is unavailable: {cwd}") from exc
    if not root.is_dir() or root.is_symlink():
        raise ConfigError(f"runtime worktree is not a safe directory: {root}")
    return root


@dataclass(frozen=True, slots=True)
class _ContextRoot:
    path: Path | None
    label: str
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class _ContextCandidate:
    path: Path
    root: Path


def _bounded_reason(value: str | None, fallback: str) -> str:
    from odoo_instance_sdk.internal.sanitize import sanitize_last_error

    return (sanitize_last_error(value) or fallback)[:240]


def _context_project_root(instance: OdooInstance, worktree: Path) -> Path:
    binding = getattr(instance, "_runtime_binding", None)
    bound = getattr(binding, "repository_root", None)
    if bound is not None:
        try:
            root = Path(bound).resolve(strict=True)
            if root.is_dir():
                return root
        except OSError:
            pass
    return worktree


def _context_roots(instance: OdooInstance, worktree: Path) -> tuple[_ContextRoot, ...]:
    """Resolve configured roots without inspecting rejected paths."""
    config = getattr(instance.config, "start_config", None)
    configured = tuple(getattr(config, "addons_path", None) or ())
    project_root = _context_project_root(instance, worktree)
    registered: tuple[Path, ...] = ()
    try:
        from odoo_instance_sdk.project import ProjectConfig

        project = ProjectConfig.load(project_root)
        registered = tuple(
            item if item.is_absolute() else project_root / item
            for item in project.addon_repositories
        )
    except Exception:
        # A manually-created instance need not have a project manifest. The
        # selected checkout remains a valid and independently useful root.
        registered = ()

    allowed = (worktree, project_root, *registered)
    values: list[tuple[str, Path, Path]] = []
    for raw in configured:
        value = Path(str(raw))
        values.append((str(raw), value if value.is_absolute() else worktree / value, worktree))
    for repository in registered:
        values.append((str(repository), repository, project_root))

    roots: list[_ContextRoot] = []
    seen: set[Path] = set()
    for label, raw, _base in values:
        if raw.is_symlink() or _has_symlink_component(raw):
            roots.append(_ContextRoot(None, label, "symlinked addon root"))
            continue
        try:
            path = raw.resolve(strict=True)
        except OSError as exc:
            roots.append(_ContextRoot(None, label, _bounded_reason(str(exc), "root unavailable")))
            continue
        if not path.is_dir():
            roots.append(_ContextRoot(None, label, "addon root is not a directory"))
            continue
        if not any(_contained(path, allowed_root) for allowed_root in allowed):
            roots.append(_ContextRoot(None, label, "addon root is outside allowed repositories"))
            continue
        if path in seen:
            continue
        seen.add(path)
        roots.append(_ContextRoot(path, label))
    return tuple(roots)


def _context_candidates(
    roots: Sequence[_ContextRoot],
) -> tuple[dict[str, tuple[_ContextCandidate, ...]], tuple[str, ...]]:
    candidates: dict[str, list[_ContextCandidate]] = {}
    warnings: list[str] = []
    for item in roots:
        if item.path is None:
            warnings.append(f"addon root {item.label!r} unavailable: {item.reason}")
            continue
        try:
            children = sorted(item.path.iterdir(), key=lambda child: child.name)
        except OSError as exc:
            warnings.append(
                f"addon root {item.label!r} unavailable: {_bounded_reason(str(exc), 'read failed')}"
            )
            continue
        for child in children:
            try:
                resolved = child.resolve(strict=True)
            except OSError:
                continue
            if (
                not _valid_name(child.name)
                or child.is_symlink()
                or _has_symlink_component(child)
                or not child.is_dir()
                or not _contained(resolved, item.path)
                or not (child / "__manifest__.py").is_file()
                or (child / "__manifest__.py").is_symlink()
            ):
                continue
            candidates.setdefault(child.name, []).append(_ContextCandidate(resolved, item.path))
    return {name: tuple(paths) for name, paths in candidates.items()}, tuple(warnings)


def _context_installed_source(names: Sequence[str]) -> str:
    # This is deliberately a read-only Odoo shell query. It reads the selected
    # database path and never imports or executes addon manifests.
    names_repr = repr(tuple(names))
    return (
        f"_odcli_names = {names_repr}\n"
        "_odcli_modules = env['ir.module.module'].search([('name', 'in', _odcli_names)], order='name')\n"
        "result = [{'name': m.name, 'state': m.state, 'installed_version': m.installed_version or None, 'latest_version': m.latest_version or None} for m in _odcli_modules]\n"
    )


def _context_step(root: Path, step_id: str, args: Sequence[str]) -> PreparedStep:
    from odoo_instance_sdk.internal.proc import PreparedStep

    return PreparedStep(
        step_id=step_id,
        argv=("git", "-C", str(root), *tuple(args)),
        cwd=str(root),
        read_only=True,
        text=True,
    )


def _result_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value or ""


def _status_changes(value: str, *, repository: str) -> tuple[ModuleGitChange, ...]:
    changes: list[ModuleGitChange] = []
    for record in value.split("\0"):
        if not record.strip():
            continue
        raw_status = record[:2]
        status = raw_status.strip()
        path = record[3:] if len(record) > 3 else ""
        if not path:
            continue
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[-1]
        if raw_status == "??":
            changes.append(
                ModuleGitChange(
                    path=path,
                    kind="untracked",
                    status=status or None,
                    repository=repository,
                )
            )
            continue
        if raw_status[:1] != " ":
            changes.append(
                ModuleGitChange(
                    path=path,
                    kind="staged",
                    status=raw_status[:1],
                    repository=repository,
                )
            )
        if raw_status[1:2] != " ":
            changes.append(
                ModuleGitChange(
                    path=path,
                    kind="unstaged",
                    status=raw_status[1:2],
                    repository=repository,
                )
            )
    return tuple(changes)


def _committed_changes(value: str, *, repository: str) -> tuple[ModuleGitChange, ...]:
    changes: list[ModuleGitChange] = []
    tokens = [token for token in value.split("\0") if token]
    index = 0
    while index < len(tokens):
        status = tokens[index].strip()
        index += 1
        if index >= len(tokens):
            break
        path = tokens[index]
        index += 1
        if status and path:
            changes.append(
                ModuleGitChange(
                    path=path,
                    kind="committed",
                    status=status,
                    repository=repository,
                )
            )
    return tuple(changes)


def _module_changes(
    candidate: _ContextCandidate,
    repository_root: str | None,
    changes: Sequence[ModuleGitChange],
) -> tuple[ModuleGitChange, ...]:
    if repository_root is None:
        return ()
    try:
        module_relative = candidate.path.relative_to(Path(repository_root))
    except ValueError:
        return ()
    selected: list[ModuleGitChange] = []
    for change in changes:
        try:
            changed = Path(change.path)
            if changed.is_absolute():
                changed = changed.relative_to(Path(repository_root))
            changed.relative_to(module_relative)
        except ValueError:
            continue
        selected.append(change)
    return tuple(selected)


def _context_result(
    candidates: Mapping[str, Sequence[_ContextCandidate]],
    warnings: Sequence[str],
    git_facts: Mapping[Path, tuple[str | None, tuple[ModuleGitChange, ...], str | None]],
    installed: Mapping[str, tuple[str | None, str | None]],
    *,
    database: ModuleAvailability,
) -> ModuleContext:
    modules: list[Module] = []
    all_git_ok = bool(git_facts)
    git_reason: str | None = None
    for _root, (_identity, _changes, reason) in git_facts.items():
        if reason is not None:
            all_git_ok = False
            git_reason = git_reason or reason

    for name in sorted(candidates):
        raw_candidates = tuple(candidates[name])
        parsed: list[tuple[_ContextCandidate, dict[str, ModuleJsonValue]]] = []
        for candidate in raw_candidates:
            try:
                parsed.append((candidate, _manifest(candidate.path)))
            except ConfigError:
                # A broken manifest must not hide facts from other modules or
                # execute arbitrary addon code while context is collected.
                continue
        if not parsed:
            continue
        candidate, manifest = parsed[0]
        repo_identity, repo_changes, _reason = git_facts.get(candidate.root, (None, (), None))
        depends = _depends(manifest, candidate.path)
        by_name = {module_name: values[0] for module_name, values in candidates.items() if values}
        dependency_details = tuple(
            ModuleDependency(
                name=dependency,
                path=str(by_name[dependency].path) if dependency in by_name else None,
                missing=dependency not in by_name,
            )
            for dependency in depends
        )
        installed_state, installed_version = installed.get(name, (None, None))
        shadowed = tuple(
            ModuleProvenance(
                name=name,
                path=str(shadow.path),
                manifest_path=str(shadow.path / "__manifest__.py"),
                repository=git_facts.get(shadow.root, (None, (), None))[0],
                repository_path=git_facts.get(shadow.root, (None, (), None))[0],
            )
            for shadow, _shadow_manifest in parsed[1:]
        )
        modules.append(
            Module(
                name=name,
                path=str(candidate.path),
                manifest_path=str(candidate.path / "__manifest__.py"),
                depends=depends,
                manifest=manifest,
                shadowed_paths=tuple(str(item.path) for item, _ in parsed[1:]),
                warnings=(
                    (f"module {name!r} is shadowed by {candidate.path}",) if len(parsed) > 1 else ()
                ),
                repository=repo_identity,
                repository_path=repo_identity,
                dependency_details=dependency_details,
                installed_state=installed_state,
                installed_version=installed_version,
                changes=_module_changes(candidate, repo_identity, repo_changes),
                shadowed=shadowed,
            )
        )

    filesystem = ModuleAvailability(
        state="available" if candidates else "unavailable",
        reason=None if candidates else "no safe addon roots",
    )
    git = ModuleAvailability(
        state="available" if all_git_ok else "unavailable",
        reason=None if all_git_ok else (git_reason or "no registered repositories"),
    )
    return ModuleContext(
        modules=tuple(modules),
        filesystem=filesystem,
        git=git,
        database=database,
        warnings=tuple(warnings),
    )


class ModuleResource:
    """Concrete module resource bound to one local Odoo instance.

    The mapping is intentionally rebuilt for each operation. This keeps
    changed worktrees and manifests observable and avoids a second cache or
    provider abstraction.
    """

    def __init__(self, instance: OdooInstance) -> None:
        self._instance = instance

    def context_command(  # noqa: C901
        self,
        *,
        executor: ProcessExecutor | None = None,
    ) -> Command[ModuleContext]:
        """Capture one read-only module context collection.

        Filesystem discovery happens before the command is captured. Git and
        selected-database facts remain separate optional sources, so a failed
        probe cannot turn already discovered modules into an empty result.
        """
        from odoo_instance_sdk.execution import Command, ExecutionPlan
        from odoo_instance_sdk.internal.proc import SubprocessExecutor

        worktree = _instance_worktree(self._instance)
        roots = _context_roots(self._instance, worktree)
        candidates, root_warnings = _context_candidates(roots)
        project_root = _context_project_root(self._instance, worktree)
        registered_repositories: tuple[Path, ...] = ()
        try:
            from odoo_instance_sdk.project import ProjectConfig

            registered_repositories = tuple(
                item if item.is_absolute() else project_root / item
                for item in ProjectConfig.load(project_root).addon_repositories
            )
        except Exception:
            pass

        def repository_probe_root(root: Path) -> Path:
            for repository in registered_repositories:
                if _contained(root, repository):
                    return repository
            return worktree

        candidates = {
            name: tuple(
                _ContextCandidate(candidate.path, repository_probe_root(candidate.root))
                for candidate in values
            )
            for name, values in candidates.items()
        }
        repository_roots: list[Path] = []
        for values in candidates.values():
            for candidate in values:
                probe_root = repository_probe_root(candidate.root)
                if probe_root not in repository_roots:
                    repository_roots.append(probe_root)
        git_steps: dict[Path, tuple[PreparedStep, PreparedStep, PreparedStep]] = {}
        steps: list[PreparedStep] = []
        for index, root in enumerate(repository_roots):
            root_step = _context_step(
                root, f"module.context.git.root.{index}", ("rev-parse", "--show-toplevel")
            )
            status_step = _context_step(
                root,
                f"module.context.git.status.{index}",
                ("status", "--porcelain=v1", "-z", "--untracked-files=all"),
            )
            committed_step = _context_step(
                root,
                f"module.context.git.committed.{index}",
                ("diff-tree", "--root", "--no-commit-id", "--name-status", "-r", "HEAD", "-z"),
            )
            git_steps[root] = (root_step, status_step, committed_step)
            steps.extend((root_step, status_step, committed_step))

        def collect_git(
            context: RunContext[ModuleContext],
        ) -> dict[Path, tuple[str | None, tuple[ModuleGitChange, ...], str | None]]:
            facts: dict[Path, tuple[str | None, tuple[ModuleGitChange, ...], str | None]] = {}
            for root, (root_step, status_step, committed_step) in git_steps.items():
                root_result = context.process(root_step.step_id)
                identity = _result_text(getattr(root_result, "stdout", "")).strip()
                root_code = getattr(root_result, "returncode", 1)
                repository = identity if root_code == 0 and identity else str(root)
                status_result = context.process(status_step.step_id)
                committed_result = context.process(committed_step.step_id)
                status_code = getattr(status_result, "returncode", 1)
                committed_code = getattr(committed_result, "returncode", 1)
                reason = None
                if root_code != 0:
                    reason = _bounded_reason(
                        _result_text(getattr(root_result, "stderr", "")),
                        "Git repository unavailable",
                    )
                elif status_code != 0:
                    reason = _bounded_reason(
                        _result_text(getattr(status_result, "stderr", "")), "Git status unavailable"
                    )
                elif committed_code != 0:
                    reason = _bounded_reason(
                        _result_text(getattr(committed_result, "stderr", "")),
                        "Git history unavailable",
                    )
                changes = (
                    _status_changes(
                        _result_text(getattr(status_result, "stdout", "")), repository=repository
                    )
                    if status_code == 0
                    else ()
                )
                if committed_code == 0:
                    changes = (
                        *changes,
                        *_committed_changes(
                            _result_text(getattr(committed_result, "stdout", "")),
                            repository=repository,
                        ),
                    )
                facts[root] = (identity or None, changes, reason)
            return facts

        def finish(
            git_facts: Mapping[Path, tuple[str | None, tuple[ModuleGitChange, ...], str | None]],
            installed: Mapping[str, tuple[str | None, str | None]],
            database: ModuleAvailability,
        ) -> ModuleContext:
            return _context_result(
                candidates,
                root_warnings,
                git_facts,
                installed,
                database=database,
            )

        start_config = getattr(self._instance.config, "start_config", None)
        database_name = getattr(start_config, "db_name", None) if start_config is not None else None
        database_name = database_name or (
            self._instance.config.configured_database_names[0]
            if len(getattr(self._instance.config, "configured_database_names", ())) == 1
            else None
        )
        shell_builder = getattr(self._instance, "_shell_script_command", None)
        if database_name and callable(shell_builder):
            git_facts: dict[Path, tuple[str | None, tuple[ModuleGitChange, ...], str | None]] = {}

            def preflight(context: RunContext[ModuleContext]) -> None:
                git_facts.update(collect_git(context))

            def convert(value: CommandResult) -> ModuleContext:
                if value.returncode != 0:
                    reason = _bounded_reason(value.stderr or value.stdout, "database query failed")
                    return finish(
                        git_facts,
                        {},
                        ModuleAvailability(state="unavailable", reason=reason),
                    )
                from odoo_instance_sdk.internal.server import parse_payload

                payload = parse_payload(value.stdout)
                raw = payload.get("result") if isinstance(payload, dict) else None
                if not isinstance(raw, list):
                    return finish(
                        git_facts,
                        {},
                        ModuleAvailability(state="unavailable", reason="invalid database response"),
                    )
                installed: dict[str, tuple[str | None, str | None]] = {}
                for item in raw:
                    if not isinstance(item, dict):
                        continue
                    name = item.get("name")
                    if not isinstance(name, str):
                        continue
                    raw_state = item.get("state")
                    state = raw_state if isinstance(raw_state, str) else None
                    raw_version = item.get("installed_version")
                    version = raw_version if isinstance(raw_version, str) else None
                    installed[name] = (state, version)
                return finish(
                    git_facts,
                    installed,
                    ModuleAvailability(state="available"),
                )

            return cast(
                "Command[ModuleContext]",
                shell_builder(
                    _context_installed_source(tuple(sorted(candidates))),
                    commit=False,
                    exclusive=False,
                    result_converter=convert,
                    preflight=preflight,
                    extra_steps=steps,
                    executor=executor,
                ),
            )

        def execute(context: RunContext[ModuleContext]) -> ModuleContext:
            git_facts = collect_git(context)
            reason = (
                "database is not selected"
                if database_name is None
                else "database probe unavailable"
            )
            return finish(
                git_facts,
                {},
                ModuleAvailability(state="unavailable", reason=reason),
            )

        return Command.create(
            ExecutionPlan(steps=tuple(step.public_projection() for step in steps)),
            execute,
            tuple(steps),
            executor=executor or SubprocessExecutor(),
        )

    def context(self) -> ModuleContext:
        return self.context_command().run()

    inspect_context = context
    module_context = context

    def _roots(self) -> tuple[Path, ...]:
        config = self._instance.config.start_config
        values = config.addons_path if config is not None else None
        worktree = _instance_worktree(self._instance)
        roots: list[Path] = []
        seen: set[Path] = set()
        for raw in values or ():
            root = _safe_root(str(raw), worktree=worktree)
            if root is not None and root not in seen:
                seen.add(root)
                roots.append(root)
        return tuple(roots)

    def _candidates(self) -> dict[str, tuple[Path, ...]]:
        worktree = _instance_worktree(self._instance)
        candidates: dict[str, list[Path]] = {}
        for root in self._roots():
            try:
                children = sorted(root.iterdir(), key=lambda item: item.name)
            except OSError as exc:
                raise ConfigError(f"cannot inspect addon root {root}: {exc}") from exc
            for child in children:
                if (
                    not _valid_name(child.name)
                    or child.is_symlink()
                    or _has_symlink_component(child)
                    or not child.is_dir()
                    or not _contained(child.resolve(), root)
                    or not _contained(child.resolve(), worktree)
                    or not (child / "__manifest__.py").is_file()
                    or (child / "__manifest__.py").is_symlink()
                ):
                    continue
                candidates.setdefault(child.name, []).append(child.resolve())
        return {name: tuple(paths) for name, paths in candidates.items()}

    def catalogue(self) -> tuple[Module, ...]:
        """Return one precedence-resolved, immutable module catalogue."""
        candidates = self._candidates()
        result: list[Module] = []
        for name, paths in candidates.items():
            path = paths[0]
            manifest = _manifest(path)
            result.append(
                Module(
                    name=name,
                    path=str(path),
                    manifest_path=str(path / "__manifest__.py"),
                    depends=_depends(manifest, path),
                    manifest=manifest,
                    shadowed_paths=tuple(str(item) for item in paths[1:]),
                    warnings=(
                        tuple(f"module {name!r} is shadowed by {path}" for path in paths[1:])
                        if len(paths) > 1
                        else ()
                    ),
                )
            )
        return tuple(sorted(result, key=lambda item: item.name))

    discover = catalogue
    list = catalogue

    @staticmethod
    def _by_name(catalogue: Sequence[Module]) -> dict[str, Module]:
        return {item.name: item for item in catalogue}

    def _resolve(self, name: str | None, catalogue: Sequence[Module]) -> Module:  # noqa: C901
        by_name = self._by_name(catalogue)
        if name is not None:
            if not _valid_name(name):
                raise ConfigError(f"invalid addon module name: {name!r}")
            module = by_name.get(name)
            if module is None:
                roots = ", ".join(str(root) for root in self._roots()) or "none"
                raise ConfigError(
                    f"addon module {name!r} was not found in safe configured roots ({roots})"
                )
            return module

        worktree = _instance_worktree(self._instance)
        current = Path.cwd().resolve()
        candidates: list[tuple[int, int, Path]] = []
        for index, root in enumerate(self._roots()):
            if not _contained(current, root):
                continue
            path = current
            distance = 0
            while _contained(path, root):
                if (
                    path != root
                    and path.is_dir()
                    and not path.is_symlink()
                    and _contained(path, worktree)
                    and (path / "__manifest__.py").is_file()
                    and not (path / "__manifest__.py").is_symlink()
                ):
                    candidates.append((distance, index, path))
                    break
                if path == root:
                    break
                path = path.parent
                distance += 1
        if not candidates:
            raise ConfigError("current directory is not inside a safe configured addon module")
        candidates.sort(key=lambda item: (item[0], item[1], str(item[2])))
        module = by_name.get(candidates[0][2].name)
        if module is None:  # pragma: no cover - catalogue and filesystem cannot disagree
            raise ConfigError("nearest addon module is not in the safe catalogue")
        return module

    def info(self, name: str | None = None) -> Module:
        return self._resolve(name, self.catalogue())

    get = info

    def where(self, name: str | None = None) -> Path:
        return Path(self.info(name).path)

    def deps(self, name: str | None = None) -> ModuleDependencies:
        catalogue = self.catalogue()
        module = self._resolve(name, catalogue)
        by_name = self._by_name(catalogue)
        return ModuleDependencies(
            module=module,
            dependencies=tuple(
                ModuleDependency(
                    name=dependency,
                    path=by_name[dependency].path if dependency in by_name else None,
                    missing=dependency not in by_name,
                )
                for dependency in module.depends
            ),
        )

    dependencies = deps

    def install_order(self, modules: str | Iterable[str]) -> ModuleInstallOrder:
        requested = (modules,) if isinstance(modules, str) else tuple(modules)
        if not requested:
            raise ConfigError("module install-order requires at least one module")
        by_name = self._by_name(self.catalogue())
        ordered: list[str] = []
        visiting: list[str] = []
        visited: set[str] = set()

        def visit(name: str) -> None:
            if name in visited:
                return
            if name in visiting:
                cycle = (*visiting[visiting.index(name) :], name)
                raise ConfigError(f"module dependency cycle: {' -> '.join(cycle)}")
            module = by_name.get(name)
            if module is None:
                parent = visiting[-1] if visiting else "requested module"
                raise ConfigError(f"missing module dependency: {parent} -> {name}")
            visiting.append(name)
            for dependency in module.depends:
                visit(dependency)
            visiting.pop()
            visited.add(name)
            ordered.append(name)

        for name in requested:
            visit(name)
        return ModuleInstallOrder(modules=tuple(ordered))

    def install_order_command(self, modules: str | Iterable[str]) -> Command[ModuleInstallOrder]:
        """Capture one immutable install-order plan for preview and execution."""
        names = (modules,) if isinstance(modules, str) else tuple(modules)
        from odoo_instance_sdk.commands.output import action_command

        return cast(
            "Command[ModuleInstallOrder]",
            action_command(
                "module.install_order",
                lambda: self.install_order(names),
                description="Plan stable module dependency install order",
                mutating=False,
            ),
        )

    def update_command(
        self,
        modules: Sequence[str],
        *,
        selection: ModuleUpdatePlan | None = None,
    ) -> Command[CommandResult]:
        names = tuple(modules)
        if not names and selection is None:
            raise ConfigError("module update requires at least one module")
        if any(not _valid_name(name) for name in names):
            raise ConfigError("module update received an invalid module name")
        from odoo_instance_sdk.internal.automation import update_modules_command

        plan = selection or self.plan_update(names)
        if not plan.modules:
            return _module_update_noop_command(plan)

        extra_steps: tuple[PreparedStep, ...] = ()
        preflights: list[Callable[[RunContext[CommandResult]], None]] = []
        if plan.not_installed:
            preflights.append(_not_installed_preflight(plan.not_installed))
        if plan.head is not None:
            from odoo_instance_sdk.internal.proc import PreparedStep

            root = _instance_worktree(self._instance)
            extra_steps = (
                PreparedStep(
                    step_id="module.update.provenance.git",
                    argv=("git", "-C", str(root), "rev-parse", "HEAD"),
                    cwd=str(root),
                    read_only=True,
                    text=True,
                ),
            )
            preflights.append(_changed_head_preflight(plan.head))

        def preflight(context: RunContext[CommandResult]) -> None:
            for validator in preflights:
                validator(context)

        if not preflights and not extra_steps:
            command = update_modules_command(self._instance, tuple(plan.modules))
        else:
            command = update_modules_command(
                self._instance,
                tuple(plan.modules),
                preflight=preflight if preflights else None,
                extra_steps=extra_steps,
            )
        if selection is None:
            return command
        from odoo_instance_sdk.execution import Command

        observation = _module_update_observation(selection)
        execution_plan = msgspec.structs.replace(
            command.plan,
            observations=(*command.plan.observations, observation),
        ).with_fingerprint()
        return Command.from_prepared(execution_plan, command._prepared())

    def plan_update(
        self,
        modules: Sequence[str],
        *,
        selection: ModuleUpdatePlan | None = None,
    ) -> ModuleUpdatePlan:
        """Capture installed-state planning through the existing module adapter."""
        names = tuple(modules)
        if not names:
            raise ConfigError("module update requires at least one module")
        from odoo_instance_sdk.internal.automation import plan_module_update

        installed = plan_module_update(self._instance, names)
        base = selection or ModuleUpdatePlan()
        return msgspec.structs.replace(
            base,
            modules=tuple(installed.modules),
            not_installed=tuple(installed.not_installed),
        )

    def changed_plan(
        self,
        runtime: RuntimeView,
        *,
        base: str | None = None,
        plan_installed: bool = True,
    ) -> ModuleUpdatePlan:
        selected = resolve_changed_selection(
            runtime.root,
            runtime.start_config,
            base=base,
            environment_base=runtime.base_ref,
            context_kind=runtime.owner_kind,
            tags=None,
        )
        plan = ModuleUpdatePlan(
            modules=tuple(selected.modules),
            base_source=selected.base_source,
            requested_base=selected.requested_base,
            resolved_base=selected.resolved_base,
            merge_base=selected.merge_base,
            head=selected.head,
            changed_files=selected.changed_files,
            ignored_paths=selected.ignored_paths,
            unmapped_paths=selected.unmapped_paths,
        )
        if not plan_installed or not plan.modules:
            return plan
        return self.plan_update(plan.modules, selection=plan)

    def changed_update_command(
        self, runtime: RuntimeView, *, base: str | None = None
    ) -> Command[CommandResult]:
        plan = self.changed_plan(runtime, base=base)
        if plan.unmapped_paths:
            raise ConfigError(
                f"changed paths are not mapped to safe addon modules: {', '.join(plan.unmapped_paths)}"
            )
        if plan.not_installed:
            raise ConfigError(f"modules not installed: {', '.join(plan.not_installed)}")
        if not plan.modules:
            return _module_update_noop_command(plan)
        return self.update_command(plan.modules, selection=plan)

    def update(self, modules: Sequence[str]) -> ModuleUpdateResult:
        command = self.update_command(modules)
        value = command.run()
        if value.returncode != 0:
            conflict = classify_module_update_error(value)
            if conflict is not None:
                raise conflict
            raise _module_update_failure(value)
        updated = _updated_names(value)
        return ModuleUpdateResult(modules=tuple(modules), updated=updated)


def _updated_names(result: CommandResult) -> tuple[str, ...]:
    from odoo_instance_sdk.internal.server import parse_payload

    payload = parse_payload(result.stdout)
    raw = payload.get("result") if isinstance(payload, dict) else None
    values = raw.get("updated") if isinstance(raw, dict) else None
    return (
        tuple(item for item in values if isinstance(item, str)) if isinstance(values, list) else ()
    )


def classify_module_update_error(
    value: CommandResult | str,
) -> ModuleOperationInProgressError | None:
    """Classify only Odoo's known concurrent-module UserError text."""
    text = value if isinstance(value, str) else f"{value.stdout}\n{value.stderr}"
    lowered = text.lower()
    is_user_error = "usererror" in lowered or "user error" in lowered
    is_concurrent = any(
        phrase in lowered
        for phrase in (
            "another module is being processed",
            "another module is currently being processed",
            "another module operation is in progress",
        )
    )
    return (
        ModuleOperationInProgressError("another Odoo module operation is in progress; retry later")
        if is_user_error and is_concurrent
        else None
    )


def _module_update_failure(value: CommandResult) -> RuntimeError:
    detail = sanitize_last_error(value.stderr) or sanitize_last_error(value.stdout)
    suffix = f": {detail}" if detail else ""
    return RuntimeError(f"module update failed (rc={value.returncode}){suffix}")


def _module_update_noop_command(plan: ModuleUpdatePlan) -> Command[CommandResult]:
    from odoo_instance_sdk.execution import Command, ExecutionPlan

    def run(_context: RunContext[CommandResult]) -> CommandResult:
        if plan.not_installed:
            raise ConfigError(f"modules not installed: {', '.join(plan.not_installed)}")
        return CommandResult(args=[], returncode=0, stdout="", stderr="", duration=0.0)

    return Command.create(
        ExecutionPlan(observations=(_module_update_observation(plan),)).with_fingerprint(),
        run,
    )


def _not_installed_preflight(
    modules: Sequence[str],
) -> Callable[[RunContext[CommandResult]], None]:
    def reject(_context: RunContext[CommandResult]) -> None:
        raise ConfigError(f"modules not installed: {', '.join(modules)}")

    return reject


def _module_update_observation(selection: ModuleUpdatePlan) -> SemanticPlanObservation:
    from odoo_instance_sdk.execution import PlanPrecondition, SemanticPlanObservation

    return SemanticPlanObservation(
        kind="semantic",
        goal="update Odoo modules",
        targets=selection.modules,
        mutations=("module update",),
        preconditions=tuple(
            PlanPrecondition(name=name, status="passed", detail=detail)
            for name, detail in (
                ("changed base", selection.resolved_base),
                ("captured HEAD", selection.head),
                ("changed paths", ", ".join(selection.changed_files)),
            )
            if detail is not None
        ),
        warnings=tuple(selection.unmapped_paths),
    )


def _changed_head_preflight(expected: str) -> Callable[[RunContext[CommandResult]], None]:
    def validate(context: RunContext[CommandResult]) -> None:
        result = context.process("module.update.provenance.git")
        raw = getattr(result, "stdout", "")
        actual = (raw.decode(errors="replace") if isinstance(raw, bytes) else raw or "").strip()
        returncode = getattr(result, "returncode", 1)
        if returncode != 0 or actual != expected:
            raise StalePlanError(
                "captured module Git revision changed",
                expected=expected,
                actual=actual or None,
            )

    return validate


__all__ = [
    "ModuleResource",
    "_module_update_failure",
    "classify_module_update_error",
]
