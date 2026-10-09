"""Thin typed composition of native Multica and core Odoo operations."""

from __future__ import annotations

import configparser
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypeVar, cast
from urllib.parse import urlsplit

from multica_py.models.project_resources import GithubRepoResourceRef, ProjectResourceRecord

from odcli_multica.client_policies import (
    askpass_command as _askpass_command,
    canonical_checkout as _canonical_checkout,
    current_branch as _current_branch,
    default_mapping_path as _default_mapping_path,
    gitlab_host as _gitlab_host,
    https_host as _https_host,
    optional_root_creator as _optional_root_creator,
    read_gitlab_credential_mappings,
    remote_host_for_operation as _remote_host_for_operation,
    resolve_root_creator as _resolve_root_creator,
    validated_project_path as _validated_project_path,
)
from odcli_multica.models import (
    MULTICA_PY_REVISION,
    MULTICA_PY_VERSION,
    ContextRequest,
    ContextVerificationError,
    GitLabCredentialContext,
    GitLabCredentialIdentity,
    MergeRequestPublicationResult,
    MulticaCompatibility,
    PreparationRequest,
    RootCreatorContext,
    VerifiedTaskContext,
)
from odoo_instance_sdk import (
    Command,
    DevelopmentEnvironment,
    EnvironmentCheckoutOptions,
    EnvironmentDatabaseMode,
    OdooClient,
    ProjectConfig,
)
from odoo_instance_sdk.commands.output import action_command
from odoo_instance_sdk.execution import ExecutionPlan
from odoo_instance_sdk.models import CommandResult, GitSyncResult

if TYPE_CHECKING:
    from multica_py import Issue, MulticaClient, OperationOptions, Page, Project, TaskRun
    from multica_py.models.system import DaemonStatus, RepositoryCheckoutResult

T_co = TypeVar("T_co", covariant=True)


class _Runnable(Protocol[T_co]):
    def run(self) -> T_co: ...


class _GitPassthrough(Protocol):
    def passthrough_command(
        self,
        args: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
        secret_values: Sequence[str] = (),
    ) -> Command[CommandResult]: ...


class _GitSync(Protocol):
    def sync_command(
        self,
        *,
        base: str | None = None,
        push: bool = False,
        environment: Mapping[str, str] | None = None,
        secret_values: Sequence[str] = (),
        remote_allowed: Callable[[str], bool] | None = None,
    ) -> Command[GitSyncResult]: ...


class GitResourceLike(_GitPassthrough, _GitSync, Protocol):
    def passthrough(self, args: Sequence[str]) -> CommandResult: ...


@dataclass(frozen=True, slots=True)
class PrepareCommand:
    """Inspectable context plus the exact captured core adoption command."""

    context: VerifiedTaskContext
    adoption_command: Command[DevelopmentEnvironment]

    @property
    def plan(self) -> ExecutionPlan:
        """Expose the core plan without introducing another plan model."""
        return self.adoption_command.plan

    def run(self) -> DevelopmentEnvironment:
        """Delegate execution to the already captured core command."""
        return self.adoption_command.run()


class MulticaOdooClient:
    """Stateless native-checkout/context/preparation client."""

    def __init__(
        self,
        core_client: OdooClient,
        multica_client: MulticaClient,
    ) -> None:
        self.core = core_client
        self.multica = multica_client
        self._compatibility = _observe_compatibility(multica_client)

    def _require_contract(self) -> None:
        identity = self._compatibility
        if not identity.observed:
            raise ContextVerificationError("typed Multica compatibility evidence is unavailable")
        if not identity.typed_checkout or not identity.typed_daemon_status:
            raise ContextVerificationError("required typed Multica capability is unavailable")
        if identity.package_version != MULTICA_PY_VERSION:
            raise ContextVerificationError("multica-py package version is ambiguous")
        if identity.package_revision != MULTICA_PY_REVISION:
            raise ContextVerificationError("multica-py package revision is ambiguous")
        match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", identity.native_cli_version)
        if match is None or tuple(int(part) for part in match.groups()) < (0, 5, 3):
            raise ContextVerificationError("native Multica CLI is below the supported floor")

    def checkout_command(
        self,
        url: str,
        *,
        ref: str | None = None,
        options: OperationOptions | None = None,
    ) -> _Runnable[RepositoryCheckoutResult]:
        """Return the typed native checkout command, never forcing ``fresh``."""
        self._require_contract()
        return self.multica.repositories.checkout_command(
            url,
            ref=ref,
            fresh=False,
            options=options,
        )

    def checkout(
        self,
        url: str,
        *,
        ref: str | None = None,
        options: OperationOptions | None = None,
    ) -> RepositoryCheckoutResult:
        """Delegate the typed native checkout convenience operation."""
        self._require_contract()
        return self.multica.repositories.checkout(url, ref=ref, fresh=False, options=options)

    def context_command(self, request: ContextRequest) -> Command[VerifiedTaskContext]:
        """Capture bounded typed reads for one explicit task context."""
        self._require_contract()
        return cast(
            "Command[VerifiedTaskContext]",
            action_command(
                "multica.context",
                lambda: self._read_context(request),
                description="Verify the native checkout, issue, run, project, and daemon context",
            ),
        )

    def context(self, request: ContextRequest) -> VerifiedTaskContext:
        """Run the finite context observation."""
        return self.context_command(request).run()

    def root_creator_command(self, context: VerifiedTaskContext) -> Command[RootCreatorContext]:
        """Capture the verified issue's finite root-creator traversal."""
        self._require_contract()
        return cast(
            "Command[RootCreatorContext]",
            action_command(
                "multica.root-creator",
                lambda: self._resolve_root_creator(context),
                description="Resolve the human creator of the verified task root issue",
            ),
        )

    def root_creator(self, context: VerifiedTaskContext) -> RootCreatorContext:
        """Resolve one human root creator from an already verified context."""
        return self.root_creator_command(context).run()

    def credential_context_command(
        self,
        context: VerifiedTaskContext,
        *,
        host: str,
        project_root: Path | str,
        mapping_path: Path | str | None = None,
        process_environment: Mapping[str, str] | None = None,
    ) -> Command[GitLabCredentialContext]:
        """Capture one private, host-scoped credential for a child command."""
        self._require_contract()
        return cast(
            "Command[GitLabCredentialContext]",
            action_command(
                "multica.credentials",
                lambda: self._resolve_credentials(
                    context,
                    host=host,
                    project_root=project_root,
                    mapping_path=mapping_path,
                    process_environment=process_environment,
                ),
                description="Resolve root-creator credentials for one GitLab host",
            ),
        )

    def credential_context(
        self,
        context: VerifiedTaskContext,
        *,
        host: str,
        project_root: Path | str,
        mapping_path: Path | str | None = None,
        process_environment: Mapping[str, str] | None = None,
    ) -> GitLabCredentialContext:
        """Resolve one private credential snapshot without persisting its token."""
        return self.credential_context_command(
            context,
            host=host,
            project_root=project_root,
            mapping_path=mapping_path,
            process_environment=process_environment,
        ).run()

    # Explicit aliases keep the identity contract discoverable to extension callers.
    resolve_root_creator = root_creator
    resolve_gitlab_credentials = credential_context

    def prepare_command(self, request: PreparationRequest) -> PrepareCommand:
        """Preflight context, then capture one exact core adoption command."""
        context = self.context(request.context)
        if not request.base_ref.strip():
            raise ContextVerificationError("preparation requires an explicit base_ref")
        selectors = sum(
            value is not None
            for value in (request.remote_name, request.backup_id, request.source_database)
        )
        if selectors != 1:
            raise ContextVerificationError(
                "preparation requires exactly one of remote_name, backup_id, or source_database"
            )
        if request.source_database is not None:
            raise ContextVerificationError(
                "caller-owned adoption supports remote_name or backup_id only"
            )
        options = EnvironmentCheckoutOptions(
            base_ref=request.base_ref,
            db_mode=EnvironmentDatabaseMode.COPY,
            remote_name=request.remote_name,
            backup_id=request.backup_id,
        )
        adoption = self.core.environments.adopt_command(
            request.context.core_project,
            Path(context.checkout_path),
            options=options,
        )
        return PrepareCommand(context=context, adoption_command=adoption)

    def prepare(self, request: PreparationRequest) -> DevelopmentEnvironment:
        """Delegate execution to the captured adoption command."""
        return self.prepare_command(request).run()

    def daemon_status_command(
        self, *, options: OperationOptions | None = None
    ) -> _Runnable[DaemonStatus]:
        """Expose the typed daemon command for callers that need inspection."""
        self._require_contract()
        return self.multica.daemon.status_command(options=options)

    def daemon_status(self, *, options: OperationOptions | None = None) -> DaemonStatus:
        """Expose the typed daemon status without decoding output locally."""
        self._require_contract()
        return self.multica.daemon.status(options=options)

    def git_command(
        self,
        context: VerifiedTaskContext,
        args: Sequence[str],
        *,
        git_resource: _GitPassthrough,
        project_root: Path | str,
        mapping_path: Path | str | None = None,
        process_environment: Mapping[str, str] | None = None,
    ) -> Command[CommandResult]:
        """Delegate raw Git transport, resolving credentials only for remotes."""
        native_args = tuple(str(value) for value in args)
        environment: Mapping[str, str] = {}
        secret_values: tuple[str, ...] = ()
        remote_host = _remote_host_for_operation(native_args, context.repository_url)
        if remote_host is not None:
            credential = self.credential_context(
                context,
                host=remote_host,
                project_root=project_root,
                mapping_path=mapping_path,
                process_environment=process_environment,
            )
            environment = credential.askpass_environment(_askpass_command())
            secret_values = (dict(credential.child_environment())["GITLAB_TOKEN"],)
        passthrough = getattr(git_resource, "passthrough_command", None)
        if not callable(passthrough):
            raise ContextVerificationError("core Git passthrough capability is unavailable")
        return cast(
            "Command[CommandResult]",
            passthrough(native_args, environment=environment, secret_values=secret_values),
        )

    def git(
        self,
        context: VerifiedTaskContext,
        args: Sequence[str],
        *,
        git_resource: _GitPassthrough,
        project_root: Path | str,
        mapping_path: Path | str | None = None,
        process_environment: Mapping[str, str] | None = None,
    ) -> CommandResult:
        return self.git_command(
            context,
            args,
            git_resource=git_resource,
            project_root=project_root,
            mapping_path=mapping_path,
            process_environment=process_environment,
        ).run()

    def sync_command(
        self,
        context: VerifiedTaskContext,
        *,
        git_resource: _GitSync,
        project_root: Path | str,
        base: str | None = None,
        push: bool = False,
        mapping_path: Path | str | None = None,
        process_environment: Mapping[str, str] | None = None,
    ) -> Command[GitSyncResult]:
        """Compose core Git sync with one root-creator credential snapshot."""
        remote_host = _remote_host_for_operation(("fetch",), context.repository_url)
        environment: Mapping[str, str] = {}
        secret_values: tuple[str, ...] = ()
        if remote_host is not None:
            credential = self.credential_context(
                context,
                host=remote_host,
                project_root=project_root,
                mapping_path=mapping_path,
                process_environment=process_environment,
            )
            environment = credential.askpass_environment(_askpass_command())
            secret_values = (dict(credential.child_environment())["GITLAB_TOKEN"],)

        sync = getattr(git_resource, "sync_command", None)
        if not callable(sync):
            raise ContextVerificationError("core Git sync capability is unavailable")

        def remote_allowed(url: str) -> bool:
            if url.startswith(("git@", "ssh://")):
                return True
            return remote_host is not None and _https_host(url) == remote_host

        return cast(
            "Command[GitSyncResult]",
            sync(
                base=base,
                push=push,
                environment=environment,
                secret_values=secret_values,
                remote_allowed=remote_allowed,
            ),
        )

    def sync(
        self,
        context: VerifiedTaskContext,
        *,
        git_resource: _GitSync,
        project_root: Path | str,
        base: str | None = None,
        push: bool = False,
        mapping_path: Path | str | None = None,
        process_environment: Mapping[str, str] | None = None,
    ) -> GitSyncResult:
        return self.sync_command(
            context,
            git_resource=git_resource,
            project_root=project_root,
            base=base,
            push=push,
            mapping_path=mapping_path,
            process_environment=process_environment,
        ).run()

    def publish_merge_request_command(
        self,
        context: VerifiedTaskContext,
        *,
        project_root: Path | str,
        git_resource: GitResourceLike | None = None,
        source_branch: str,
        target_branch: str,
        title: str,
        description_file: Path | str,
        assignee: str | None = None,
        project_path: str | None = None,
        mapping_path: Path | str | None = None,
        process_environment: Mapping[str, str] | None = None,
    ) -> Command[MergeRequestPublicationResult]:
        """Capture a dry-runnable GitLab create-or-update MR operation."""
        from odcli_multica.gitlab import (
            publish_merge_request,
            read_description_file,
            repository_project_path,
        )
        from odoo_instance_sdk.execution import ExecutionPlan
        from odoo_instance_sdk.internal.proc import PreparedAction, RunContext

        host, inferred_project = repository_project_path(context.repository_url)
        selected_project = _validated_project_path(project_path, inferred_project)
        description = read_description_file(description_file)

        context_step = PreparedAction(
            step_id="gitlab.mr.revalidate-context",
            action="gitlab.mr.revalidate-context",
            description="Revalidate the verified task root before provider mutation",
            read_only=True,
            details={
                "issue_id": context.issue_id,
                "root_issue_id": context.root_issue_id,
                "root_creator_id": context.root_creator_id,
                "host": host,
                "project_path": selected_project,
            },
        )
        branch_step = PreparedAction(
            step_id="gitlab.mr.revalidate-branch",
            action="gitlab.mr.revalidate-branch",
            description="Revalidate the current checkout branch before provider mutation",
            read_only=True,
            details={"expected_branch": source_branch},
        )
        publish_step = PreparedAction(
            step_id="gitlab.mr.publish",
            action="gitlab.mr.publish",
            description="Resolve and create or update one exact GitLab merge request",
            mutating=True,
            details={
                "issue_id": context.issue_id,
                "project_path": selected_project,
                "source_branch": source_branch,
                "target_branch": target_branch,
                "title": title,
                "description_file": str(Path(description_file).resolve()),
                "assignee": assignee,
            },
        )
        prepared_steps = (context_step, branch_step, publish_step)

        def operation(
            run_context: RunContext[MergeRequestPublicationResult],
        ) -> MergeRequestPublicationResult:
            run_context.action(context_step.step_id)
            current_host, current_inferred_project = repository_project_path(context.repository_url)
            current_project = _validated_project_path(project_path, current_inferred_project)
            if current_host != host or current_project != selected_project:
                raise ContextVerificationError("GitLab repository context changed")
            root = self._resolve_root_creator(context)
            if context.root_issue_id is not None and root.root_issue_id != context.root_issue_id:
                raise ContextVerificationError("task root issue changed")
            if (
                context.root_creator_id is not None
                and root.root_creator_id != context.root_creator_id
            ):
                raise ContextVerificationError("task root creator changed")
            run_context.complete_action(context_step.step_id)

            if git_resource is not None:
                run_context.action(branch_step.step_id)
                current_branch = _current_branch(git_resource)
                if current_branch != source_branch:
                    raise ContextVerificationError("current Git branch changed")
                run_context.complete_action(branch_step.step_id)

            run_context.action(publish_step.step_id)
            credential = self.credential_context(
                context,
                host=host,
                project_root=project_root,
                mapping_path=mapping_path,
                process_environment=process_environment,
            )
            server_url = getattr(self.multica.config, "server_url", None)
            if not isinstance(server_url, str) or not server_url.strip():
                raise ContextVerificationError("Multica issue URL is unavailable")
            result = publish_merge_request(
                credential=credential,
                project_path=selected_project,
                source_branch=source_branch,
                target_branch=target_branch,
                title=title,
                description=description,
                issue_url=server_url.rstrip("/") + f"/issues/{context.issue_id}",
                assignee=assignee,
            )
            run_context.complete_action(publish_step.step_id)
            return result

        executable_steps = (
            prepared_steps if git_resource is not None else (context_step, publish_step)
        )
        return Command.create(
            ExecutionPlan(
                steps=tuple(step.public_projection() for step in executable_steps)
            ).with_fingerprint(),
            operation,
            executable_steps,
        )

    def publish_merge_request(
        self,
        context: VerifiedTaskContext,
        *,
        project_root: Path | str,
        source_branch: str,
        target_branch: str,
        title: str,
        description_file: Path | str,
        assignee: str | None = None,
        project_path: str | None = None,
        mapping_path: Path | str | None = None,
        process_environment: Mapping[str, str] | None = None,
    ) -> MergeRequestPublicationResult:
        return self.publish_merge_request_command(
            context,
            project_root=project_root,
            source_branch=source_branch,
            target_branch=target_branch,
            title=title,
            description_file=description_file,
            assignee=assignee,
            project_path=project_path,
            mapping_path=mapping_path,
            process_environment=process_environment,
        ).run()

    def _read_context(self, request: ContextRequest) -> VerifiedTaskContext:
        checkout = _canonical_checkout(request.checkout_path)
        project: Project = self.multica.projects.get(request.multica_project)
        issue: Issue = self.multica.issues.get(request.issue)
        if issue.project_id != project.id:
            raise ContextVerificationError("issue is not a member of the selected Multica project")
        if issue.project_id != request.multica_project:
            raise ContextVerificationError("issue project identity conflicts with the request")
        run = _find_run(self.multica.issues.runs(request.issue), request.run, request.issue)
        if run.project_id is not None and run.project_id != project.id:
            raise ContextVerificationError("run project identity conflicts with the request")
        workspace_id = _required(run.workspace_id, "run workspace identity")
        if request.workspace_id is not None and request.workspace_id != workspace_id:
            raise ContextVerificationError("requested workspace conflicts with the run")
        if project.workspace_id != workspace_id:
            raise ContextVerificationError("project and run workspace identities conflict")
        configured_workspace = self.multica.config.workspace_id
        if configured_workspace is not None and configured_workspace != workspace_id:
            raise ContextVerificationError("scoped Multica workspace conflicts with the run")
        runtime_id = _required(run.runtime_id, "run runtime identity")
        if request.runtime_id is not None and request.runtime_id != runtime_id:
            raise ContextVerificationError("requested runtime conflicts with the run")
        task_root = _task_root(run, checkout)
        core_repository_url = _verify_core_project(request.core_project)
        try:
            resources = self.multica.projects.resources.list(project.id)
        except Exception as exc:
            raise ContextVerificationError("project repository evidence is unavailable") from exc
        repository_url = _repository_url(
            resources,
            project.id,
            core_repository_url,
            request.core_repository_url or request.repository_url,
        )
        _verify_run_snapshot(run, project.id, repository_url)
        daemon = self.multica.daemon.status()
        daemon_id = _required(daemon.daemon_id, "daemon identity")
        _verify_daemon(
            daemon,
            workspace_id,
            runtime_id,
            task_root,
            checkout,
            expected_server_url=self.multica.config.server_url,
        )
        root_creator = _optional_root_creator(
            issue,
            request.issue,
            workspace_id,
            project.id,
            self.multica.issues,
        )
        return VerifiedTaskContext(
            checkout_path=str(checkout),
            task_root=str(task_root),
            repository_url=repository_url,
            workspace_id=workspace_id,
            multica_project_id=project.id,
            issue_id=request.issue,
            run_id=request.run,
            runtime_id=runtime_id,
            daemon_id=daemon_id,
            observed_at=datetime.now(UTC),
            root_issue_id=root_creator.root_issue_id if root_creator else None,
            root_creator_id=root_creator.root_creator_id if root_creator else None,
        )

    def _resolve_root_creator(self, context: VerifiedTaskContext) -> RootCreatorContext:
        return _resolve_root_creator(
            context,
            self.multica.issues,
        )

    def _resolve_credentials(
        self,
        context: VerifiedTaskContext,
        *,
        host: str,
        project_root: Path | str,
        mapping_path: Path | str | None,
        process_environment: Mapping[str, str] | None,
    ) -> GitLabCredentialContext:
        root = self._resolve_root_creator(context)
        try:
            normalized_host = _gitlab_host(host)
        except ValueError as exc:
            raise ContextVerificationError("GitLab host is invalid") from exc
        mappings = read_gitlab_credential_mappings(
            Path(mapping_path)
            if mapping_path is not None
            else _default_mapping_path(Path(project_root))
        )
        matches = tuple(
            item
            for item in mappings
            if item.user_id == root.root_creator_id and item.host == normalized_host
        )
        if not matches:
            raise ContextVerificationError("GitLab credential mapping is unavailable")
        if len(matches) != 1:
            raise ContextVerificationError("GitLab credential mapping is ambiguous")
        mapping = matches[0]
        try:
            from odoo_instance_sdk.internal.project_env import (
                effective_project_environment,
                load_project_environment,
            )

            file_values = load_project_environment(project_root)
            effective = effective_project_environment(file_values, process_environment)
        except Exception as exc:
            raise ContextVerificationError("project credential environment is unavailable") from exc
        token = effective.get(mapping.token_key, "")
        if not token:
            raise ContextVerificationError(
                f"GitLab credential token key {mapping.token_key} is unavailable"
            )
        identity = GitLabCredentialIdentity(
            user_id=mapping.user_id,
            host=mapping.host,
            login=mapping.login,
            token_key=mapping.token_key,
            workspace_id=root.workspace_id,
            issue_id=root.issue_id,
            root_issue_id=root.root_issue_id,
        )
        return GitLabCredentialContext(identity=identity, _token=token)


def _observe_compatibility(multica: MulticaClient) -> MulticaCompatibility:
    """Observe the public package, capability, and daemon contract once."""
    try:
        daemon = multica.daemon.status()
    except AttributeError as exc:
        raise ContextVerificationError("typed daemon status capability is unavailable") from exc
    direct_url_text = metadata.distribution("multica-py").read_text("direct_url.json")
    if direct_url_text is None:
        raise ContextVerificationError("multica-py revision evidence is unavailable")
    direct_url = json.loads(direct_url_text)
    vcs_info = direct_url.get("vcs_info") if isinstance(direct_url, dict) else None
    revision = vcs_info.get("commit_id") if isinstance(vcs_info, dict) else None
    if not isinstance(revision, str):
        raise ContextVerificationError("multica-py revision evidence is unavailable")
    repositories = getattr(multica, "repositories", None)
    daemon_resource = getattr(multica, "daemon", None)
    return MulticaCompatibility(
        package_version=metadata.version("multica-py"),
        package_revision=revision,
        native_cli_version=daemon.cli_version or "",
        typed_checkout=callable(getattr(repositories, "checkout_command", None))
        and callable(getattr(repositories, "checkout", None)),
        typed_daemon_status=callable(getattr(daemon_resource, "status_command", None))
        and callable(getattr(daemon_resource, "status", None)),
        observed=True,
    )


def _find_run(page: Page[TaskRun], run_id: str, issue_id: str) -> TaskRun:
    if (
        page.has_more is not False
        or page.next_cursor is not None
        or page.offset not in (None, 0)
        or page.total is None
        or page.total != len(page.items)
    ):
        raise ContextVerificationError("run evidence is paginated and incomplete")
    for run in page.items:
        if run.id == run_id:
            if run.issue_id != issue_id:
                raise ContextVerificationError("run issue identity conflicts with the request")
            return run
    raise ContextVerificationError("run is not present in the selected issue")


def _required(value: str | None, label: str) -> str:
    if value is None or not value.strip():
        raise ContextVerificationError(f"{label} is unavailable")
    return value


def _task_root(run: TaskRun, checkout: Path) -> Path:
    candidates = (run.work_dir, run.durable_work_dir)
    for value in candidates:
        if value is None:
            continue
        root = Path(value)
        if not root.is_absolute():
            continue
        try:
            canonical = root.resolve(strict=True)
        except OSError:
            continue
        if checkout == canonical or checkout.is_relative_to(canonical):
            return canonical
    raise ContextVerificationError(
        "checkout is not contained by an absolute current or durable task directory"
    )


def _verify_core_project(path: Path) -> str:
    if not path.is_dir():
        raise ContextVerificationError("core project path is not a directory")
    try:
        project = ProjectConfig.load(path)
    except Exception as exc:
        raise ContextVerificationError("core project repository identity is unavailable") from exc
    if project.repository_root.resolve() != path.resolve():
        raise ContextVerificationError("core project repository identity conflicts with its path")
    try:
        config_path = _git_config_path(path)
        parser = configparser.ConfigParser(interpolation=None)
        if not config_path.is_file():
            raise ContextVerificationError("core project Git identity is unavailable")
        parser.read(config_path, encoding="utf-8")
        origin = parser.get('remote "origin"', "url", fallback="").strip()
    except (OSError, configparser.Error) as exc:
        raise ContextVerificationError("core project Git identity is unavailable") from exc
    if not origin:
        raise ContextVerificationError("core project Git identity is unavailable")
    return origin


def _git_config_path(path: Path) -> Path:
    git_marker = path / ".git"
    if git_marker.is_dir():
        return git_marker / "config"
    if not git_marker.is_file():
        raise ContextVerificationError("core project Git identity is unavailable")
    marker = git_marker.read_text(encoding="utf-8").strip()
    if not marker.lower().startswith("gitdir:"):
        raise ContextVerificationError("core project Git identity is unavailable")
    git_dir = (path / marker.split(":", 1)[1].strip()).resolve()
    common_dir = git_dir / "commondir"
    if common_dir.is_file():
        git_dir = (git_dir / common_dir.read_text(encoding="utf-8").strip()).resolve()
    return git_dir / "config"


def _repository_url(
    page: Page[ProjectResourceRecord], project_id: str, core_url: str, selected: str | None
) -> str:
    if page.has_more or page.next_cursor is not None:
        raise ContextVerificationError("project repository evidence is paginated and incomplete")
    if page.offset not in (None, 0) or page.total is None or page.total != len(page.items):
        raise ContextVerificationError("project repository evidence is incomplete")
    values = tuple(
        resource.resource_ref.url
        for resource in page.items
        if resource.project_id == project_id
        and resource.resource_type == "github_repo"
        and isinstance(resource.resource_ref, GithubRepoResourceRef)
    )
    if len(values) != 1:
        raise ContextVerificationError("repository identity is absent or ambiguous")
    native = values[0]
    if _canonical_repository_url(core_url) != _canonical_repository_url(native):
        raise ContextVerificationError("core repository does not match the native repository")
    if selected is not None and _canonical_repository_url(selected) != _canonical_repository_url(
        core_url
    ):
        raise ContextVerificationError("selected repository does not match the core repository")
    return native


def _verify_run_snapshot(run: TaskRun, project_id: str, repository_url: str) -> None:
    if run.project_id is not None and run.project_id != project_id:
        raise ContextVerificationError("run project identity conflicts with the request")
    if not run.project_resources:
        return
    values = tuple(
        resource.resource_ref
        for resource in run.project_resources
        if resource.resource_type in {"github_repo", "git_repository", "repository"}
        and isinstance(resource.resource_ref, str)
    )
    if len(values) != 1 or _canonical_repository_url(values[0]) != _canonical_repository_url(
        repository_url
    ):
        raise ContextVerificationError("run repository snapshot conflicts with project resources")


def _repository_authority(host: str, port: int | None) -> str:
    host = host.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{host}:{port}" if port is not None else host


def _canonical_repository_url(value: str) -> str:
    raw = value.strip()
    if raw.startswith("git@") and ":" in raw:
        authority, path = raw.split(":", 1)
        host = authority[4:]
        if not host or "/" in host or not path or path.startswith("/"):
            raise ContextVerificationError("repository URL is unsupported")
        return f"{host.lower()}/{path.strip('/').removesuffix('.git').lower()}"

    parsed = urlsplit(raw)
    if parsed.scheme not in {"https", "ssh"} or not parsed.netloc:
        raise ContextVerificationError("repository URL is unsupported")
    if parsed.query or parsed.fragment:
        raise ContextVerificationError("repository URL is unsupported")
    try:
        parsed_host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ContextVerificationError("repository URL is unsupported") from exc
    if parsed_host is None or not parsed.path.strip("/"):
        raise ContextVerificationError("repository URL is unsupported")
    if parsed.scheme == "https":
        if parsed.username is not None or parsed.password is not None:
            raise ContextVerificationError("repository URL userinfo is unsupported")
    elif parsed.username != "git" or parsed.password is not None:
        raise ContextVerificationError("repository URL userinfo is unsupported")
    return f"{_repository_authority(parsed_host, port)}/{parsed.path.strip('/').removesuffix('.git').lower()}"


def _canonical_server_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ContextVerificationError("daemon server identity is unsupported")
    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ContextVerificationError("daemon server identity is unsupported")
    try:
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ContextVerificationError("daemon server identity is unsupported") from exc
    if host is None:
        raise ContextVerificationError("daemon server identity is unsupported")
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme.lower()}://{_repository_authority(host, port)}{path}"


def _verify_daemon(
    daemon: DaemonStatus,
    workspace_id: str,
    runtime_id: str,
    task_root: Path,
    checkout: Path,
    *,
    expected_server_url: str | None,
) -> None:
    _verify_daemon_identity(daemon, expected_server_url)
    _verify_daemon_filesystem(daemon, workspace_id, runtime_id, task_root, checkout)


def _verify_daemon_identity(daemon: DaemonStatus, expected_server_url: str | None) -> None:
    if daemon.status.lower() not in {"ready", "running", "active"}:
        raise ContextVerificationError("owning daemon is not ready")
    if daemon.cli_version is None:
        raise ContextVerificationError("daemon CLI compatibility evidence is unavailable")
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", daemon.cli_version)
    if match is None or tuple(int(part) for part in match.groups()) < (0, 5, 3):
        raise ContextVerificationError("native Multica CLI is below the supported floor")
    if daemon.server_url is None or not daemon.server_url.strip():
        raise ContextVerificationError("daemon server identity is unavailable")
    if expected_server_url is not None and _canonical_server_url(
        daemon.server_url
    ) != _canonical_server_url(expected_server_url):
        raise ContextVerificationError("daemon server identity conflicts with the client scope")


def _verify_daemon_filesystem(
    daemon: DaemonStatus,
    workspace_id: str,
    runtime_id: str,
    task_root: Path,
    checkout: Path,
) -> None:
    if daemon.workspaces is None:
        raise ContextVerificationError("daemon filesystem evidence is unavailable")
    workspace = next((item for item in daemon.workspaces if item.id == workspace_id), None)
    if workspace is None or runtime_id not in workspace.runtimes:
        raise ContextVerificationError("daemon does not own the selected runtime")
    if workspace.path is None or not Path(workspace.path).is_absolute():
        raise ContextVerificationError("daemon workspace has no absolute filesystem evidence")
    try:
        workspace_root = Path(workspace.path).resolve(strict=True)
    except OSError as exc:
        raise ContextVerificationError(
            "daemon workspace filesystem evidence is unavailable"
        ) from exc
    if not task_root.is_relative_to(workspace_root):
        raise ContextVerificationError("task directory is outside the daemon workspace")
    if not checkout.is_relative_to(task_root):
        raise ContextVerificationError("checkout is outside the verified task directory")


__all__ = [
    "GitResourceLike",
    "MulticaOdooClient",
    "PrepareCommand",
    "read_gitlab_credential_mappings",
]
