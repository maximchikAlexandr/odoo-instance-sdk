"""Repository-scoped GitLab merge-request publication."""

from __future__ import annotations

import mimetypes
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, cast

from odoo_instance_sdk.exceptions import GitLabPublicationError, GitWorkflowError
from odoo_instance_sdk.internal.git_frontier import credentials as resolve_credentials
from odoo_instance_sdk.internal.git_repository import resolve_repository, revalidate_repository
from odoo_instance_sdk.internal.gitlab_publication import (
    FrozenDescription,
    FrozenImage,
    existing_owned_image,
    image_identity,
    read_description_file,
    replace_owned_image,
)
from odoo_instance_sdk.internal.transport.gitlab import GitLabHttpClient
from odoo_instance_sdk.models import (
    GitLabCredentialContext,
    GitLabIdentity,
    GitMergeRequestResult,
    GitUploadResult,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import Command, JsonValue
    from odoo_instance_sdk.internal.gitlab_identity import GitLabUserTransport
    from odoo_instance_sdk.internal.proc import PreparedAction, PrivateJsonValue, RunContext


class GitLabPublicationTransport(Protocol):
    def get_project(
        self, credential: GitLabCredentialContext, project: str
    ) -> Mapping[str, JsonValue]: ...

    def list_open_merge_requests(
        self,
        credential: GitLabCredentialContext,
        project_id: int,
        source_branch: str,
        target_branch: str,
    ) -> tuple[Mapping[str, JsonValue], ...]: ...

    def upload_project_file(
        self,
        credential: GitLabCredentialContext,
        project_id: int,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> Mapping[str, JsonValue]: ...

    def create_merge_request(
        self,
        credential: GitLabCredentialContext,
        project_id: int,
        *,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str,
    ) -> Mapping[str, JsonValue]: ...

    def update_merge_request(
        self,
        credential: GitLabCredentialContext,
        project_id: int,
        iid: int,
        *,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str,
    ) -> Mapping[str, JsonValue]: ...


def _action(
    step_id: str,
    description: str,
    *,
    read_only: bool = False,
    mutating: bool = False,
    details: Mapping[str, JsonValue] | None = None,
) -> PreparedAction:
    from odoo_instance_sdk.internal.proc import PreparedAction

    return PreparedAction(
        step_id=step_id,
        action=step_id,
        description=description,
        read_only=read_only,
        mutating=mutating,
        details=cast("PrivateJsonValue", dict(details or {})),
    )


def _required_text(value: JsonValue | None, message: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GitLabPublicationError(message)
    return value.strip()


def _result(
    payload: Mapping[str, JsonValue], *, source: str, target: str, outcome: str
) -> GitMergeRequestResult:
    iid = payload.get("iid")
    url = payload.get("web_url")
    if not isinstance(iid, int) or isinstance(iid, bool) or not isinstance(url, str) or not url:
        raise GitLabPublicationError("GitLab merge-request returned an invalid result")
    return GitMergeRequestResult(
        project=_required_text(payload.get("project_path"), "GitLab project is unavailable")
        if isinstance(payload.get("project_path"), str)
        else "",
        merge_request_id=iid,
        web_url=url,
        source_branch=source,
        target_branch=target,
        outcome=cast("Literal['created', 'updated']", outcome),
    )


class GitLabMergeRequestMixin:
    """Methods mixed into :class:`~odoo_instance_sdk.resources.git.GitResource`."""

    def merge_request_command(  # noqa: C901
        self,
        *,
        repository: Path | str,
        member_id: str,
        target: str,
        title: str,
        description_file: Path | str,
        image: Path | str | None = None,
        credential: GitLabCredentialContext | None = None,
        identity: GitLabIdentity | None = None,
        identity_transport: GitLabUserTransport
        | Callable[[str, str], Mapping[str, JsonValue]]
        | None = None,
        user_root: Path | str | None = None,
        mapping_path: Path | str | None = None,
        environment_path: Path | str | None = None,
        process_environment: Mapping[str, str] | None = None,
        publication_transport: GitLabPublicationTransport | None = None,
    ) -> Command[GitMergeRequestResult]:
        from odoo_instance_sdk.execution import Command, ExecutionPlan

        root = Path(repository).expanduser().resolve()
        snapshot = resolve_repository(root)
        if snapshot.branch is None:
            raise GitWorkflowError("merge-request source branch is detached")
        target_branch = _required_text(target, "merge-request target branch is required")
        merge_title = _required_text(title, "merge-request title is required")
        if "\n" in merge_title or "\r" in merge_title:
            raise GitLabPublicationError("merge-request title must be one line")
        description_path = Path(description_file).expanduser()
        frozen_description: FrozenDescription = read_description_file(description_path)
        description = frozen_description.text
        frozen_image: FrozenImage | None = None
        if image is not None:
            frozen_image = image_identity(image)
        selected, verified = resolve_credentials(
            member_id,
            snapshot.host,
            credential=credential,
            identity=identity,
            user_root=user_root,
            mapping_path=mapping_path,
            environment_path=environment_path,
            process_environment=process_environment,
            identity_transport=identity_transport,
        )
        transport = publication_transport or GitLabHttpClient()
        lookup = _action(
            "gitlab.mr.lookup",
            "Resolve the exact GitLab project and matching open merge requests",
            read_only=True,
            details={
                "project": snapshot.project,
                "source_branch": snapshot.branch,
                "target_branch": target_branch,
                "member_id": selected.user_id,
                "gitlab_user_id": verified.user_id,
            },
        )
        actions: list[PreparedAction] = [lookup]
        upload_action: PreparedAction | None = None
        if frozen_image is not None:
            upload_action = _action(
                "gitlab.mr.upload-image",
                "Upload the selected image to the GitLab project",
                mutating=True,
                details={"digest": frozen_image.digest, "project": snapshot.project},
            )
            actions.append(upload_action)
        mutate = _action(
            "gitlab.mr.mutate",
            "Create or update the unique GitLab merge request",
            mutating=True,
            details={
                "project": snapshot.project,
                "source_branch": snapshot.branch,
                "target_branch": target_branch,
            },
        )
        actions.append(mutate)
        public_steps = tuple(action.public_projection() for action in actions)
        plan = ExecutionPlan(steps=public_steps).with_fingerprint()

        def _publish(context: RunContext[GitMergeRequestResult]) -> GitMergeRequestResult:
            revalidate_repository(snapshot)
            context.action(lookup.step_id)
            project_payload = transport.get_project(selected, snapshot.project)
            project_id = project_payload.get("id")
            if not isinstance(project_id, int) or isinstance(project_id, bool):
                raise GitLabPublicationError("GitLab project lookup returned an invalid id")
            matches = transport.list_open_merge_requests(
                selected, project_id, snapshot.branch or "", target_branch
            )
            if len(matches) > 1:
                identifiers = ", ".join(
                    str(item.get("iid")) for item in matches if isinstance(item.get("iid"), int)
                )
                raise GitLabPublicationError(
                    f"multiple matching open merge requests: {identifiers}"
                )
            existing = matches[0] if matches else None
            existing_description = existing.get("description") if existing is not None else None
            existing_description = (
                existing_description if isinstance(existing_description, str) else ""
            )
            final_description = description
            uploaded: GitUploadResult | None = None
            if frozen_image is not None and upload_action is not None:
                owned = existing_owned_image(existing_description)
                if owned is not None and owned[0] == frozen_image.digest:
                    markdown = owned[1]
                    context.skip(upload_action.step_id)
                else:
                    context.action(upload_action.step_id)
                    content_type = (
                        mimetypes.guess_type(frozen_image.filename)[0] or "application/octet-stream"
                    )
                    payload = transport.upload_project_file(
                        selected,
                        project_id,
                        frozen_image.filename,
                        frozen_image.content,
                        content_type,
                    )
                    markdown = _required_text(
                        payload.get("markdown"), "GitLab project upload returned no Markdown link"
                    )
                    uploaded = GitUploadResult(
                        project=snapshot.project, markdown=markdown, digest=frozen_image.digest
                    )
                    context.complete_action(upload_action.step_id)
                final_description = replace_owned_image(
                    description, digest=frozen_image.digest, markdown=markdown
                )
            context.complete_action(lookup.step_id)
            context.action(mutate.step_id)
            try:
                if existing is None:
                    payload = transport.create_merge_request(
                        selected,
                        project_id,
                        source_branch=snapshot.branch or "",
                        target_branch=target_branch,
                        title=merge_title,
                        description=final_description,
                    )
                    outcome = "created"
                else:
                    iid = existing.get("iid")
                    if not isinstance(iid, int) or isinstance(iid, bool):
                        raise GitLabPublicationError(  # noqa: TRY301
                            "matching merge request has an invalid IID"
                        )
                    payload = transport.update_merge_request(
                        selected,
                        project_id,
                        iid,
                        source_branch=snapshot.branch or "",
                        target_branch=target_branch,
                        title=merge_title,
                        description=final_description,
                    )
                    outcome = "updated"
            except Exception as exc:
                if uploaded is not None:
                    raise GitLabPublicationError(
                        "merge-request mutation failed after image upload; possible orphan upload"
                    ) from exc
                raise
            context.complete_action(mutate.step_id)
            return _result(
                {**payload, "project_path": snapshot.project},
                source=snapshot.branch or "",
                target=target_branch,
                outcome=outcome,
            )

        def callback(context: RunContext[GitMergeRequestResult]) -> GitMergeRequestResult:
            return _publish(context)

        return Command.create(plan, callback, tuple(actions))

    def merge_request(
        self,
        *,
        repository: Path | str,
        member_id: str,
        target: str,
        title: str,
        description_file: Path | str,
        image: Path | str | None = None,
        credential: GitLabCredentialContext | None = None,
        identity: GitLabIdentity | None = None,
        identity_transport: GitLabUserTransport
        | Callable[[str, str], Mapping[str, JsonValue]]
        | None = None,
        user_root: Path | str | None = None,
        mapping_path: Path | str | None = None,
        environment_path: Path | str | None = None,
        process_environment: Mapping[str, str] | None = None,
        publication_transport: GitLabPublicationTransport | None = None,
    ) -> GitMergeRequestResult:
        return self.merge_request_command(
            repository=repository,
            member_id=member_id,
            target=target,
            title=title,
            description_file=description_file,
            image=image,
            credential=credential,
            identity=identity,
            identity_transport=identity_transport,
            user_root=user_root,
            mapping_path=mapping_path,
            environment_path=environment_path,
            process_environment=process_environment,
            publication_transport=publication_transport,
        ).run()


__all__ = ["GitLabMergeRequestMixin", "GitLabPublicationTransport"]
