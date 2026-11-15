"""Typed client for OpenSpec changes and environment bindings."""

from __future__ import annotations

from pathlib import Path

from odcli_openspec.adapter import parse_changes, progress_from_parts, require_change_id
from odcli_openspec.errors import OpenSpecError
from odcli_openspec.models import (
    OpenSpecBinding,
    OpenSpecChange,
    OpenSpecProgress,
    UnbindResult,
)
from odcli_openspec.probes import (
    assess_probe,
    bind_action,
    binding_from_progress,
    failure_reason,
    openspec_executable,
    probe_steps,
    run_probe,
    trusted_executable,
)
from odcli_openspec.store import delete_binding, read_binding, write_binding
from odoo_instance_sdk import OdooClient
from odoo_instance_sdk.execution import (
    ActionStep,
    Command,
    ExecutionStep,
    ExternalEffects,
    JsonValue,
    ProcessStep,
    external_command,
)
from odoo_instance_sdk.models import DevelopmentEnvironment

_PROBE_TIMEOUT = 5.0


class OpenSpecChanges:
    """Read active OpenSpec changes from one worktree.

    Args:
        core: Public OdCLI client. Retained for the combined client shape.
    """

    def __init__(self, core: OdooClient) -> None:
        self._core = core

    def list_changes_command(
        self, *, cwd: Path | None = None
    ) -> Command[tuple[OpenSpecChange, ...]]:
        """Capture ``openspec list --json`` in one command.

        Args:
            cwd: Worktree to probe. Defaults to the current directory.

        Returns:
            The captured list command.
        """
        root = str((cwd or Path.cwd()).resolve())
        executable = openspec_executable()
        step = ProcessStep(
            step_id="openspec.list",
            argv=(executable, "list", "--json"),
            display=f"{executable} list --json",
            executable=executable,
            cwd=root,
            timeout=_PROBE_TIMEOUT,
            read_only=True,
        )

        def execute(effects: ExternalEffects) -> tuple[OpenSpecChange, ...]:
            try:
                output = effects.process("openspec.list")
            except Exception as exc:
                raise OpenSpecError(failure_reason(exc), "OpenSpec list failed") from exc
            if output.returncode != 0:
                raise OpenSpecError("non_zero", "OpenSpec list failed")
            return parse_changes(output.stdout)

        return external_command((step,), execute)

    def list_changes(self, *, cwd: Path | None = None) -> tuple[OpenSpecChange, ...]:
        """Run the captured list command.

        Args:
            cwd: Worktree to probe. Defaults to the current directory.

        Returns:
            Active changes from that worktree.
        """
        return self.list_changes_command(cwd=cwd).run()


class OpenSpecEnvironments:
    """Bind changes and read per-environment progress.

    Args:
        core: Public OdCLI client used to resolve environments.
    """

    def __init__(self, core: OdooClient) -> None:
        self._core = core

    def bind_command(self, change: str, *, cwd: Path | None = None) -> Command[OpenSpecBinding]:
        """Capture validation and binding for the current environment.

        Args:
            change: OpenSpec change id.
            cwd: Directory used to resolve the environment.

        Returns:
            The captured bind command.
        """
        require_change_id(change)
        environment = _resolve_environment(self._core, cwd)
        executable = openspec_executable()
        steps: tuple[ExecutionStep, ...] = (
            *probe_steps(
                prefix="openspec.bind-probe",
                executable=executable,
                change_id=change,
                cwd=environment.worktree_path,
                timeout=_PROBE_TIMEOUT,
            ),
            bind_action(str(environment.id), change),
        )

        def execute(effects: ExternalEffects) -> OpenSpecBinding:
            outputs, process_reason = run_probe(effects, "openspec.bind-probe")
            progress = assess_probe(
                change_id=change,
                outputs=outputs,
                process_reason=process_reason,
            )
            effects.begin("openspec.bind")
            if progress.binding_state != "bound":
                error = OpenSpecError(
                    progress.reason or "invalid_change", "OpenSpec binding failed"
                )
                effects.fail("openspec.bind", error)
                raise error
            try:
                binding = binding_from_progress(
                    environment_id=str(environment.id),
                    executable=executable,
                    progress=progress,
                )
                write_binding(binding)
            except OpenSpecError as exc:
                effects.fail("openspec.bind", exc)
                raise
            except OSError as exc:
                error = OpenSpecError("binding_io", "OpenSpec binding file could not be written")
                effects.fail("openspec.bind", error)
                raise error from exc
            effects.complete("openspec.bind")
            return binding

        return external_command(steps, execute)

    def bind(self, change: str, *, cwd: Path | None = None) -> OpenSpecBinding:
        """Run the captured bind command.

        Args:
            change: OpenSpec change id.
            cwd: Directory used to resolve the environment.

        Returns:
            The stored binding.
        """
        return self.bind_command(change, cwd=cwd).run()

    def unbind_command(self, *, cwd: Path | None = None) -> Command[UnbindResult]:
        """Capture deletion of the current environment's binding.

        Args:
            cwd: Directory used to resolve the environment.

        Returns:
            The captured unbind command.
        """
        environment = _resolve_environment(self._core, cwd)
        step = ActionStep(
            step_id="openspec.unbind",
            action="delete_binding",
            description="Remove the OpenSpec environment binding",
            details={"environment_id": str(environment.id)},
            mutating=True,
        )

        def execute(effects: ExternalEffects) -> UnbindResult:
            effects.begin("openspec.unbind")
            removed = delete_binding(environment.id)
            effects.complete("openspec.unbind")
            return UnbindResult(environment_id=str(environment.id), removed=removed)

        return external_command((step,), execute)

    def unbind(self, *, cwd: Path | None = None) -> UnbindResult:
        """Run the captured unbind command.

        Args:
            cwd: Directory used to resolve the environment.

        Returns:
            Whether a binding file was removed.
        """
        return self.unbind_command(cwd=cwd).run()

    def progress_command(self) -> Command[tuple[OpenSpecProgress, ...]]:
        """Capture one progress probe for every catalog environment.

        Returns:
            The captured progress command. Unbound environments do not launch
            OpenSpec.
        """
        environments = tuple(self._core.environments.list())
        steps: list[ProcessStep] = []
        bound_ids: list[str] = []
        probed: set[str] = set()
        for environment in environments:
            binding = read_binding(environment.id)
            if binding is None:
                continue
            environment_id = str(environment.id)
            try:
                executable = trusted_executable(binding.executable)
            except OpenSpecError:
                bound_ids.append(environment_id)
                continue
            prefix = f"openspec.progress.{environment.id}"
            steps.extend(
                probe_steps(
                    prefix=prefix,
                    executable=executable,
                    change_id=binding.change_id,
                    cwd=environment.worktree_path,
                    timeout=_PROBE_TIMEOUT,
                )
            )
            bound_ids.append(environment_id)
            probed.add(environment_id)
        bound_values: list[JsonValue] = list(bound_ids)
        action = ActionStep(
            step_id="openspec.progress",
            action="collect_progress",
            description="Collect OpenSpec progress for catalog environments",
            details={"bound_environment_ids": bound_values},
            read_only=True,
        )

        def execute(effects: ExternalEffects) -> tuple[OpenSpecProgress, ...]:
            rows: list[OpenSpecProgress] = []
            effects.begin("openspec.progress")
            try:
                for environment in environments:
                    rows.append(_progress_for_environment(environment, effects, probed))
            except Exception as exc:
                effects.fail("openspec.progress", exc)
                raise
            effects.complete("openspec.progress")
            return tuple(rows)

        planned: tuple[ExecutionStep, ...] = (*steps, action)
        return external_command(planned, execute)

    def progress(self) -> tuple[OpenSpecProgress, ...]:
        """Run the captured progress command.

        Returns:
            One progress row per catalog environment.
        """
        return self.progress_command().run()


class OpenSpecOdooClient:
    """Public composition of the core client and OpenSpec operations.

    Args:
        core: Public OdCLI client.
    """

    def __init__(self, core: OdooClient) -> None:
        self.openspec = OpenSpecChanges(core)
        self.environments = OpenSpecEnvironments(core)


def _resolve_environment(core: OdooClient, cwd: Path | None) -> DevelopmentEnvironment:
    base = (cwd or Path.cwd()).resolve()
    matches: list[DevelopmentEnvironment] = []
    for environment in core.environments.list():
        worktree = Path(environment.worktree_path).resolve()
        try:
            base.relative_to(worktree)
        except ValueError:
            continue
        matches.append(environment)
    if not matches:
        raise OpenSpecError(
            "environment_unresolved",
            "No registered environment contains the current directory",
        )
    matches.sort(key=lambda item: len(Path(item.worktree_path).resolve().parts), reverse=True)
    return matches[0]


def _progress_for_environment(
    environment: DevelopmentEnvironment,
    effects: ExternalEffects,
    probed: set[str],
) -> OpenSpecProgress:
    binding = read_binding(environment.id)
    if binding is None:
        return _unbound()
    if str(environment.id) not in probed:
        return progress_from_parts(
            binding_state="unavailable",
            change_id=binding.change_id,
            root=None,
            version=None,
            reason="missing_executable",
        )
    prefix = f"openspec.progress.{environment.id}"
    outputs, process_reason = run_probe(effects, prefix)
    return assess_probe(
        change_id=binding.change_id,
        outputs=outputs,
        process_reason=process_reason,
    )


def _unbound() -> OpenSpecProgress:
    return progress_from_parts(
        binding_state="unbound",
        change_id=None,
        root=None,
        version=None,
        reason=None,
    )
