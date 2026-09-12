"""Safe, on-demand discovery and update planning for Odoo addons."""

from __future__ import annotations

import ast
import keyword
from collections.abc import Callable, Iterable, Mapping, Sequence
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
    ModuleDependencies,
    ModuleDependency,
    ModuleInstallOrder,
    ModuleJsonValue,
    ModuleUpdatePlan,
    ModuleUpdateResult,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.commands.context import RuntimeView
    from odoo_instance_sdk.execution import Command, SemanticPlanObservation
    from odoo_instance_sdk.internal.proc import PreparedStep, RunContext
    from odoo_instance_sdk.resources.instance import OdooInstance


type _LiteralValue = (
    None
    | bool
    | int
    | float
    | str
    | list["_LiteralValue"]
    | tuple["_LiteralValue", ...]
    | dict[str, "_LiteralValue"]
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


class ModuleResource:
    """Concrete module resource bound to one local Odoo instance.

    The mapping is intentionally rebuilt for each operation. This keeps
    changed worktrees and manifests observable and avoids a second cache or
    provider abstraction.
    """

    def __init__(self, instance: OdooInstance) -> None:
        self._instance = instance

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
