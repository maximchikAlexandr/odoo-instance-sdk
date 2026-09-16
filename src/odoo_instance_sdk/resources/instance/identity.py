from __future__ import annotations

# ruff: noqa: F821
import contextlib
import os
import sys
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

import psutil

import odoo_instance_sdk.resources.instance as _instance_shim
from odoo_instance_sdk.exceptions import (
    InstanceConfigurationError,
)
from odoo_instance_sdk.internal.locks import exclusive_lock, shared_lock
from odoo_instance_sdk.internal.proc import (
    PreparedAction,
    PreparedStep,
    ProcessExecutor,
    ProcessHandle,
    ProcessResult,
    wait_foreground,
)
from odoo_instance_sdk.internal.process_env import (
    captured_child_environment,
)
from odoo_instance_sdk.internal.server import (
    _write_secret_config,
    cleanup_secret_config,
)
from odoo_instance_sdk.models import (
    CommandResult,
    OdooProcess,
    StartConfig,
)
from odoo_instance_sdk.resources.database import DatabaseResource
from odoo_instance_sdk.resources.instance import helpers as _helpers

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import (
        Command,
    )
    from odoo_instance_sdk.internal.proc import PrivateJsonValue, RunContext

globals().update(
    {name: value for name, value in _helpers.__dict__.items() if not name.startswith("__")}
)

SubprocessExecutor = _instance_shim.SubprocessExecutor
terminate = _instance_shim.terminate


class _IdentityMixin:
    def __post_init__(self) -> None:
        self.databases = DatabaseResource(
            base_url=self.config.base_url,
            master_password=self.config.master_password,
            _instance=self,
        )
        from odoo_instance_sdk.resources.git import GitResource
        from odoo_instance_sdk.resources.module import ModuleResource

        self.modules = ModuleResource(self)
        self.git = GitResource(self)

    def __repr__(self) -> str:
        return f"OdooInstance(base_url={self.config.base_url!r}, databases=<DatabaseResource>)"

    def _dependency_manifest(
        self,
    ) -> tuple[tuple[PreparedStep | PreparedAction, ...], Path | None]:
        if self._postgres_cluster is None:
            return (), None
        from odoo_instance_sdk.resources.postgres import PostgresCluster

        if not isinstance(self._postgres_cluster, PostgresCluster):
            return (), None
        temporary_path: Path | None = None
        if self._postgres_cluster.mode == "compose":
            compose_file = self._postgres_cluster.compose_file
            temporary_path = compose_file.parent / f".compose-{uuid.uuid4().hex}.yaml.tmp"
        steps = self._postgres_cluster._ensure_running_steps(60.0, temporary_path=temporary_path)
        return tuple(steps), temporary_path

    def _ensure_dependencies_ready(
        self,
        context: RunContext[T] | None = None,
        *,
        dependency_steps: Sequence[PreparedStep | PreparedAction] = (),
        temporary_path: Path | None = None,
    ) -> None:
        """Dependency preflight: ensure project PostgresCluster is ready before spawn.

        Called exactly once per public spawn entrypoint. The exclusive shell
        mutator calls it after claiming the artifact lock, so its cluster
        recheck is serialized with other artifact operations. External-mode
        clusters are probed only; compose-mode clusters are started if stopped.
        Manual instances (``instance(base_url=...)`` / ``from_config()``) have
        ``_postgres_cluster is None`` and skip preflight.
        """
        if self._postgres_cluster is None:
            return
        from odoo_instance_sdk.resources.postgres import PostgresCluster

        if isinstance(self._postgres_cluster, PostgresCluster):
            step_ids = {
                step.step_id: step.step_id
                for step in dependency_steps
                if isinstance(step, PreparedStep)
            }
            self._postgres_cluster._ensure_running_impl(
                60.0,
                temporary_path=temporary_path,
                step_ids=step_ids,
            )
            if context is not None:
                self._postgres_cluster._account_optional_steps(context, dependency_steps)
            return
        self._postgres_cluster.ensure_running(timeout=60.0)

    def _executable_prefix(self) -> tuple[str, ...]:
        if self.config.command_prefix is not None:
            return self.config.command_prefix
        if self.config.deferred_runtime is not None:
            return self.config.deferred_runtime.command_prefix()
        return (self._client.config.executable,)

    def run(
        self,
        args: list[str],
        *,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> CommandResult:
        return self.run_command(args, cwd=cwd, env=env, timeout=timeout).run()

    def run_command(
        self,
        args: list[str],
        *,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> Command[CommandResult]:

        argv = (*self._executable_prefix(), *tuple(args))
        environment_snapshot, environment_overrides = captured_child_environment(
            env, project_environment=self.config.project_environment
        )
        child_secrets = _child_secret_values(self.config.project_environment, env)
        step = PreparedStep(
            step_id="instance.run",
            argv=argv,
            cwd=None if cwd is None else str(cwd),
            environment=environment_overrides,
            environment_snapshot=environment_snapshot,
            environment_overrides=environment_overrides,
            secret_values=child_secrets,
            timeout=timeout,
            read_only=True,
        )

        def execute(context: RunContext[CommandResult]) -> CommandResult:
            result = cast("ProcessResult", context.process(step.step_id))
            return _command_result(result, timeout, step)

        from odoo_instance_sdk.execution import Command

        return Command.create(
            _command_plan((step,)),
            execute,
            (step,),
            executor=SubprocessExecutor(),
        )

    def start(
        self,
        config: StartConfig | None = None,
        *,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
    ) -> OdooProcess:
        return self.start_command(config, cwd=cwd, env=env).run()

    def start_command(
        self,
        config: StartConfig | None = None,
        *,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
    ) -> Command[OdooProcess]:
        if config is None:
            config = self.config.start_config
            if config is None:
                raise InstanceConfigurationError(
                    "No StartConfig — pass one explicitly or create instance via from_config()"
                )
        snapshot, cli_args, secret_path, secrets = _snapshot_start_inputs(config)
        argv = (*self._executable_prefix(), *cli_args)
        resolved_cwd = cwd if cwd is not None else self.config.default_cwd
        environment_snapshot, environment_overrides = captured_child_environment(
            env, project_environment=self.config.project_environment
        )
        secrets = (*secrets, *_child_secret_values(self.config.project_environment, env))
        step = PreparedStep(
            step_id="instance.start",
            argv=argv,
            # Environment-owned starts must use the captured runtime cwd when
            # the caller does not override it.  Otherwise the child starts in
            # the caller's directory while the persisted identity records the
            # environment cwd, making a later public stop fail closed.
            cwd=None if resolved_cwd is None else str(resolved_cwd),
            environment=environment_overrides,
            environment_snapshot=environment_snapshot,
            environment_overrides=environment_overrides,
            mode="long-running",
            secret_values=secrets,
            long_running=True,
            start_new_session=True,
            inherit_stdio=True,
        )
        dependency_steps, dependency_temporary_path = self._dependency_manifest()
        prepared_steps: tuple[PreparedStep | PreparedAction, ...] = (
            *dependency_steps,
            step,
        )

        def execute(context: RunContext[OdooProcess]) -> OdooProcess:
            _assert_http_port_free(config)
            self._ensure_dependencies_ready(
                context,
                dependency_steps=dependency_steps,
                temporary_path=dependency_temporary_path,
            )
            secret_created = False
            try:
                if secret_path is not None:
                    _write_secret_config(snapshot, secret_path)
                    secret_created = True
                handle = context.spawn(step.step_id)
                proc = OdooProcess(
                    id=uuid.uuid4().hex,
                    pid=handle.pid,
                    args=list(argv),
                    started_at=time.time(),
                )
                self._client.register_process(proc, handle.process, secret_path)
            except BaseException:
                if secret_created:
                    cleanup_secret_config(secret_path)
                raise
            else:
                return proc

        from odoo_instance_sdk.execution import Command

        return Command.create(
            _command_plan(prepared_steps, secrets=secrets),
            execute,
            prepared_steps,
            executor=SubprocessExecutor(),
        )

    def run_foreground(
        self,
        config: StartConfig | None = None,
        *,
        args: Sequence[str] = (),
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
    ) -> int:
        return self.run_foreground_command(config, args=args, cwd=cwd, env=env).run()

    def run_foreground_command(  # noqa: C901
        self,
        config: StartConfig | None = None,
        *,
        args: Sequence[str] = (),
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
    ) -> Command[int]:
        if config is None:
            config = self.config.start_config
            if config is None:
                raise InstanceConfigurationError(
                    "No StartConfig — pass one explicitly or create instance via from_config()"
                )
        validated_args = resolve_runtime_argv_extra(self.config.default_run_args, args)
        resolved_cwd = cwd if cwd is not None else self.config.default_cwd
        snapshot, cli_args, secret_path, secrets = _snapshot_start_inputs(config)
        environment_snapshot, environment_overrides = captured_child_environment(
            env, project_environment=self.config.project_environment
        )
        secrets = (*secrets, *_child_secret_values(self.config.project_environment, env))
        step = PreparedStep(
            step_id="instance.foreground",
            argv=(*self._executable_prefix(), *cli_args, *validated_args),
            cwd=None if resolved_cwd is None else str(resolved_cwd),
            environment=environment_overrides,
            environment_snapshot=environment_snapshot,
            environment_overrides=environment_overrides,
            mode="foreground",
            secret_values=secrets,
            long_running=True,
            start_new_session=True,
            inherit_stdio=True,
        )
        from odoo_instance_sdk.internal.proc import PreparedStep as _PreparedStep

        dependency_steps, dependency_temporary_path = self._dependency_manifest()
        prepared_steps: tuple[PreparedStep | PreparedAction, ...] = (*dependency_steps, step)
        if (
            self._runtime_binding is not None or self._environment_id is not None
        ) and resolved_cwd is not None:
            target = str(resolved_cwd)
            prepared_steps += (
                _PreparedStep(
                    step_id="instance.foreground.git.branch",
                    argv=("git", "-C", target, "rev-parse", "--abbrev-ref", "HEAD"),
                    cwd=target,
                    timeout=10.0,
                    read_only=True,
                ),
                _PreparedStep(
                    step_id="instance.foreground.git.commit",
                    argv=("git", "-C", target, "rev-parse", "HEAD"),
                    cwd=target,
                    timeout=10.0,
                    read_only=True,
                ),
            )

        process_executor = SubprocessExecutor()

        def execute(context: RunContext[int]) -> int:
            # The planning probe is intentionally repeated at this mutation
            # boundary.  A stale preview must never turn into a spawn.
            if type(process_executor) is SubprocessExecutor:
                _assert_http_port_free(config)
            self._ensure_dependencies_ready(
                context,
                dependency_steps=dependency_steps,
                temporary_path=dependency_temporary_path,
            )
            for dependency_step in dependency_steps:
                if context.planned(dependency_step.step_id) and not context.consumed(
                    dependency_step.step_id
                ):
                    context.skip(dependency_step.step_id)
            with self._artifact_lock():
                secret_created = False
                if secret_path is not None:
                    _write_secret_config(snapshot, secret_path)
                    secret_created = True
                handle: ProcessHandle | None = None
                try:
                    handle = context.spawn(step.step_id)
                    if self._runtime_binding is not None or self._environment_id is not None:
                        self._persist_runtime_identity(
                            handle.pid,
                            snapshot,
                            resolved_cwd,
                            context=context,
                        )
                    from odoo_instance_sdk.internal.server import wait_foreground_process

                    return wait_foreground_process(
                        handle,
                    )
                except BaseException:
                    if handle is not None:
                        with contextlib.suppress(BaseException):
                            terminate(
                                handle,
                                process_group_id=handle.process_group_id,
                                timeout=5.0,
                            )
                    raise
                finally:
                    self._clear_runtime_identity()
                    if secret_created:
                        cleanup_secret_config(secret_path)

        from odoo_instance_sdk.execution import Command

        return Command.create(
            _command_plan(
                prepared_steps,
                secrets=secrets,
                observations=(
                    _http_port_observation(
                        config,
                        environment_id=self._environment_id,
                        client=self._client,
                    ),
                ),
            ),
            execute,
            prepared_steps,
            executor=process_executor,
        )

    def _clear_runtime_identity(self) -> None:
        """Best-effort cleanup of the persisted runtime identity.
        Catalog cleanup failures are diagnostic only."""
        binding = self._runtime_binding
        environment_id = self._environment_id
        if binding is None and environment_id is None:
            return
        try:
            catalog = cast("_RuntimeCatalog", self._client.get_catalog())
            owner = _runtime_owner(binding, environment_id)
            catalog._clear_runtime(*owner)
        except Exception as e:
            print(f"failed to clear environment runtime: {e}", file=sys.stderr)

    def _persist_runtime_identity(
        self,
        root_pid: int,
        config: StartConfig,
        cwd: str | Path | None,
        context: RunContext[T] | None = None,
    ) -> None:
        """Persist the exact runtime identity before foreground waiting begins."""
        binding = self._runtime_binding
        environment_id = self._environment_id
        if binding is None and environment_id is None:
            return
        create_time = _process_create_time(root_pid)
        checkout_branch, commit_sha = _worktree_ref(cwd, context=context)
        http_url = f"http://{config.http_interface}:{config.http_port}"
        catalog = cast("_RuntimeCatalog", self._client.get_catalog())
        if binding is not None:
            catalog._register_project(
                binding.project_id, binding.repository_root, binding.git_common_dir
            )
            catalog._upsert_runtime(
                binding.owner_kind,
                binding.owner_id,
                root_pid=root_pid,
                create_time=create_time,
                started_at=datetime.now(UTC).isoformat(),
                checkout_branch=checkout_branch,
                commit_sha=commit_sha,
                http_url=http_url,
                http_port=config.http_port,
                database_name=config.db_name or "",
            )
            return
        assert environment_id is not None
        catalog._upsert_runtime(
            "environment",
            environment_id,
            root_pid=root_pid,
            create_time=create_time,
            started_at=datetime.now(UTC).isoformat(),
            checkout_branch=checkout_branch,
            commit_sha=commit_sha,
            http_url=http_url,
            http_port=config.http_port,
            database_name=config.db_name or "",
        )

    def iter_logs(self, *, tail: int = 100, follow: bool = False) -> Iterator[str]:
        """Yield the last ``tail`` lines of the bound logfile, optionally following appends."""
        if tail < 1:
            raise InstanceConfigurationError("tail must be >= 1")
        config = self.config.start_config
        if config is None:
            raise InstanceConfigurationError(
                "No StartConfig — create instance via from_config() or from_environment()"
            )
        raw = config.logfile
        if raw is None or not raw.strip():
            raise InstanceConfigurationError(
                "logfile is absent or empty; set logfile in the bound odoo.conf"
            )
        path = (self.config.default_cwd or Path.cwd()) / raw.strip()
        yield from _iter_logfile(path, tail=tail, follow=follow)

    def shell(self, *, args: Sequence[str] = ()) -> int:
        return self.shell_command(args=args).run()

    def shell_command(self, *, args: Sequence[str] = ()) -> Command[int]:
        config = self.config.start_config
        if config is None:
            raise InstanceConfigurationError(
                "No StartConfig — create instance via from_config() or from_environment()"
            )
        validated_args = _validate_runtime_args(args)
        snapshot, cli_args, secret_path, secrets = _snapshot_start_inputs(config)
        full_args = (*self._executable_prefix(), "shell", *cli_args, *validated_args)
        resolved_cwd = self.config.default_cwd
        environment_snapshot, environment_overrides = captured_child_environment(
            None, project_environment=self.config.project_environment
        )
        secrets = (*secrets, *_child_secret_values(self.config.project_environment))
        step = PreparedStep(
            step_id="instance.shell",
            argv=full_args,
            cwd=None if resolved_cwd is None else str(resolved_cwd),
            environment=environment_overrides,
            environment_snapshot=environment_snapshot,
            environment_overrides=environment_overrides,
            mode="foreground",
            secret_values=secrets,
            interactive=True,
            long_running=True,
            start_new_session=True,
            inherit_stdio=True,
        )
        dependency_steps, dependency_temporary_path = self._dependency_manifest()
        prepared_steps: tuple[PreparedStep | PreparedAction, ...] = (
            *dependency_steps,
            step,
        )

        def execute(context: RunContext[int]) -> int:
            self._ensure_dependencies_ready(
                context,
                dependency_steps=dependency_steps,
                temporary_path=dependency_temporary_path,
            )
            for dependency_step in dependency_steps:
                if context.planned(dependency_step.step_id) and not context.consumed(
                    dependency_step.step_id
                ):
                    context.skip(dependency_step.step_id)
            with self._artifact_lock():
                secret_created = False
                if secret_path is not None:
                    _write_secret_config(snapshot, secret_path)
                    secret_created = True
                try:
                    handle = context.spawn(step.step_id)
                    return wait_foreground(handle)
                finally:
                    if secret_created:
                        cleanup_secret_config(secret_path)

        from odoo_instance_sdk.execution import Command

        return Command.create(
            _command_plan(prepared_steps, secrets=secrets),
            execute,
            prepared_steps,
            executor=SubprocessExecutor(),
        )

    def run_shell_script(
        self,
        source: str,
        *,
        argv: Sequence[str] = (),
        timeout: float | None = None,
        commit: bool = False,
    ) -> CommandResult:
        return self.run_shell_script_command(
            source, argv=argv, timeout=timeout, commit=commit
        ).run()

    def run_shell_script_command(
        self,
        source: str,
        *,
        argv: Sequence[str] = (),
        timeout: float | None = None,
        commit: bool = False,
    ) -> Command[CommandResult]:
        return self._shell_script_command(
            source, argv=argv, timeout=timeout, commit=commit, exclusive=False
        )

    def _shell_script_command(
        self,
        source: str,
        *,
        argv: Sequence[str] = (),
        timeout: float | None = None,
        commit: bool = False,
        exclusive: bool,
        result_converter: Callable[[CommandResult], T] | None = None,
        callback_override: Callable[[], T] | None = None,
        preflight: Callable[[RunContext[T]], None] | None = None,
        extra_steps: Sequence[PreparedStep | PreparedAction] = (),
        executor: ProcessExecutor | None = None,
    ) -> Command[T]:
        config = self.config.start_config
        if config is None:
            raise InstanceConfigurationError(
                "No StartConfig — create instance via from_config() or from_environment()"
            )
        step, snapshot, secret_path, secrets = _build_shell_script_step(
            config,
            executable_prefix=self._executable_prefix(),
            default_cwd=self.config.default_cwd,
            source=source,
            argv=argv,
            timeout=timeout,
            commit=commit,
            project_environment=self.config.project_environment,
        )
        dependency_steps, dependency_temporary_path = self._dependency_manifest()
        action = PreparedAction(
            step_id="instance.shell_script.transaction",
            action="commit" if commit else "rollback",
            description="Commit or roll back the Odoo shell transaction",
            details={"commit": commit},
            read_only=not commit,
            mutating=commit,
        )

        captured_steps = (*dependency_steps, *extra_steps, step, action)

        def execute(context: RunContext[T]) -> T:
            # Stale-plan validation is deliberately the first operation.  In
            # particular it must precede readiness, lock acquisition, and
            # secret-config creation so a preview cannot turn into a partial
            # execution when its provenance has changed.
            if preflight is not None:
                preflight(context)

            def run_inside_lock() -> T:
                secret_created = False
                if secret_path is not None:
                    _write_secret_config(snapshot, secret_path)
                    secret_created = True
                try:
                    if callback_override is not None:
                        context.action(action.step_id)
                        converted_result = callback_override()
                        context.complete_action(action.step_id)
                        return converted_result
                    context.action(action.step_id)
                    result = cast("ProcessResult", context.process(step.step_id))
                    converted = _command_result(result, timeout, step)
                    converted_result = (
                        result_converter(converted)
                        if result_converter is not None
                        else cast("T", converted)
                    )
                    context.complete_action(action.step_id)
                    return converted_result
                finally:
                    if secret_created:
                        cleanup_secret_config(secret_path)

            if exclusive:
                with self._artifact_operation(exclusive=True):
                    self._ensure_dependencies_ready(
                        context,
                        dependency_steps=dependency_steps,
                        temporary_path=dependency_temporary_path,
                    )
                    return run_inside_lock()
            self._ensure_dependencies_ready(
                context,
                dependency_steps=dependency_steps,
                temporary_path=dependency_temporary_path,
            )
            with self._artifact_lock():
                return run_inside_lock()

        from odoo_instance_sdk.execution import Command

        return Command.create(
            _command_plan(captured_steps, secrets=secrets),
            execute,
            captured_steps,
            executor=executor or SubprocessExecutor(),
        )

    def _run_shell_script_in_context(
        self,
        context: RunContext[PrivateJsonValue],
        source: str,
        *,
        argv: Sequence[str] = (),
        timeout: float | None = None,
        commit: bool = False,
    ) -> CommandResult:
        """Consume a shell step from an already-owned command ledger.

        Database preparation owns the lock and the command snapshot.  Calling
        ``run_shell_script_command().run()`` from that callback would silently
        create a second ledger, so this small adapter deliberately mirrors the
        captured step construction and consumes it on the active context.
        """
        config = self.config.start_config
        if config is None:
            raise InstanceConfigurationError(
                "No StartConfig — create instance via from_config() or from_environment()"
            )
        captured = context.prepared("instance.shell_script")
        runtime_step, snapshot, secret_path, _ = _build_shell_script_step(
            config,
            executable_prefix=self._executable_prefix(),
            default_cwd=self.config.default_cwd,
            source=source,
            argv=argv,
            timeout=timeout,
            commit=commit,
            nonce=captured.wrapper_nonce,
            secret_config_path=captured.secret_config_path,
            project_environment=self.config.project_environment,
        )
        # The active command owns the complete immutable process input.  Even
        # seemingly harmless late binding (argv, cwd, environment, stdin,
        # timeout, or mode) would turn an inspected child into a different
        # child, so reject it before the executor is reached.
        if runtime_step != captured:
            from odoo_instance_sdk.exceptions import UnplannedStepError

            raise UnplannedStepError(captured.step_id, reason="shell inputs changed after capture")
        secret_created = False
        if secret_path is not None:
            _write_secret_config(snapshot, secret_path)
            secret_created = True
        try:
            result = cast("ProcessResult", context.process_prepared(captured))
            return _command_result(result, timeout, captured)
        finally:
            if secret_created:
                cleanup_secret_config(secret_path)

    def _run_shell_script_exclusive(
        self,
        source: str,
        *,
        argv: Sequence[str] = (),
        timeout: float | None = None,
        commit: bool = False,
    ) -> CommandResult:
        """Internal mutator entrypoint; lock choice belongs to this instance only."""
        from odoo_instance_sdk.internal.proc import active_context

        context = active_context()
        if context is not None:
            return self._run_shell_script_in_context(
                context,
                source,
                argv=argv,
                timeout=timeout,
                commit=commit,
            )
        return self._shell_script_command(
            source, argv=argv, timeout=timeout, commit=commit, exclusive=True
        ).run()

    @contextlib.contextmanager
    def _artifact_lock(self) -> Iterator[None]:
        with self._artifact_operation(exclusive=False):
            yield

    @contextlib.contextmanager
    def _artifact_operation(self, *, exclusive: bool) -> Iterator[None]:
        if self._artifact_lock_path is None:
            yield
            return
        lock = exclusive_lock if exclusive else shared_lock
        with lock(self._artifact_lock_path):
            yield

    def _read_runtime_identity(self) -> _RuntimeIdentity | None:
        """Read one environment runtime identity, including a live-process snapshot."""
        environment_id = self._environment_id
        if environment_id is None:
            raise InstanceConfigurationError("stop requires an environment-owned runtime")
        catalog = cast("_RuntimeCatalog", self._client.get_catalog())
        runtime_row = catalog.get_environment_runtime(environment_id)
        if runtime_row is None:
            return None
        try:
            root_pid = int(str(runtime_row["root_pid"]))
            create_time = float(str(runtime_row["create_time"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("runtime identity record is unreadable") from exc

        env_row = catalog.get_environment(environment_id)
        if env_row is None:
            raise RuntimeError("environment identity record is unavailable")
        expected_executable, expected_argv, expected_cwd, config_path = _runtime_expectations(
            env_row
        )

        def vanished_identity() -> _RuntimeIdentity:
            return _RuntimeIdentity(
                environment_id=environment_id,
                root_pid=root_pid,
                create_time=create_time,
                expected_executable=expected_executable,
                expected_argv=expected_argv,
                expected_cwd=expected_cwd,
                expected_config_path=config_path,
                live_create_time=None,
                live_executable=None,
                live_argv=None,
                live_cwd=None,
                live_config_path=None,
                process_group_id=None,
            )

        try:
            process = psutil.Process(root_pid)
        except psutil.NoSuchProcess:
            return vanished_identity()
        except (psutil.AccessDenied, psutil.ZombieProcess, OSError) as exc:
            raise RuntimeError("runtime identity is inaccessible") from exc

        try:
            live_create_time = float(process.create_time())
            live_executable = _canonical_runtime_path(process.exe())
            live_argv = tuple(process.cmdline())
            live_cwd = _canonical_runtime_path(process.cwd())
            process_group_id = os.getpgid(root_pid) if sys.platform != "win32" else None
        except psutil.NoSuchProcess:
            return vanished_identity()
        except (psutil.AccessDenied, psutil.ZombieProcess, OSError, TypeError) as exc:
            raise RuntimeError("runtime identity is inaccessible") from exc
        return _RuntimeIdentity(
            environment_id=environment_id,
            root_pid=root_pid,
            create_time=create_time,
            expected_executable=expected_executable,
            expected_argv=expected_argv,
            expected_cwd=expected_cwd,
            expected_config_path=config_path,
            live_create_time=live_create_time,
            live_executable=live_executable,
            live_argv=_canonical_runtime_argv(live_argv),
            live_cwd=live_cwd,
            live_config_path=(
                _canonical_runtime_path(config_arg)
                if (config_arg := _runtime_config_arg(live_argv)) is not None
                else None
            ),
            process_group_id=process_group_id,
        )

    @staticmethod
    def _validate_runtime_identity(identity: _RuntimeIdentity) -> None:
        if identity.vanished:
            return
        mismatches: list[str] = []
        if identity.live_create_time != identity.create_time:
            mismatches.append("create_time")
        if identity.live_executable != identity.expected_executable:
            mismatches.append("executable")
        if not _runtime_argv_matches(identity):
            mismatches.append("argv")
        if identity.live_cwd != identity.expected_cwd:
            mismatches.append("cwd")
        if identity.live_config_path != identity.expected_config_path:
            mismatches.append("config")
        if sys.platform != "win32" and identity.process_group_id != identity.root_pid:
            mismatches.append("process_group")
        if mismatches:
            raise RuntimeError(f"runtime identity mismatch: {', '.join(mismatches)}")
