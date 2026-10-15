from __future__ import annotations

import hashlib
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest

from odoo_instance_sdk.client import OdooClient
from odoo_instance_sdk.config import InstanceConfig, OdooClientConfig
from odoo_instance_sdk.exceptions import GitLabPublicationError
from odoo_instance_sdk.execution import JsonValue
from odoo_instance_sdk.models import (
    GitLabCredentialContext,
    GitLabCredentialIdentity,
    GitLabIdentity,
)
from odoo_instance_sdk.resources.git import GitResource
from odoo_instance_sdk.resources.gitlab_mr import GitLabPublicationTransport
from odoo_instance_sdk.resources.instance import OdooInstance

MEMBER = "11111111-1111-4111-8111-111111111111"


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
        env=os.environ | {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull},
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _repo(root: Path) -> None:
    root.mkdir()
    _git(root, "init", "-q", "-b", "feature")
    _git(root, "config", "user.email", "tests@example.test")
    _git(root, "config", "user.name", "tests")
    _git(root, "remote", "add", "origin", "https://gitlab.example/team/repo")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-qm", "base")


def _resource(root: Path) -> GitResource:
    client = OdooClient(config=OdooClientConfig(executable="odoo"))
    instance = OdooInstance(
        config=InstanceConfig(base_url="http://127.0.0.1:8069", default_cwd=root),
        _client=client,
    )
    return GitResource(instance)


def _identity() -> tuple[GitLabCredentialContext, GitLabIdentity]:
    credential = GitLabCredentialContext(
        identity=GitLabCredentialIdentity(
            user_id=MEMBER,
            host="gitlab.example",
            login="alice",
            token_key="ODCLI_GITLAB_TOKEN_ALICE",
        ),
        _token="private-token",
    )
    return credential, GitLabIdentity(
        user_id=7,
        username="alice",
        name="Alice Example",
        email="alice@example.test",
        host="gitlab.example",
    )


class _Transport(GitLabPublicationTransport):
    def __init__(
        self,
        matches: tuple[Mapping[str, JsonValue], ...] = (),
        *,
        upload_markdown: str = "![image](/uploads/image.png)",
        mutation_error: Exception | None = None,
    ) -> None:
        self.matches = matches
        self.upload_markdown = upload_markdown
        self.mutation_error = mutation_error
        self.calls: list[str] = []
        self.mutation_descriptions: list[str] = []

    def get_project(
        self, _credential: GitLabCredentialContext, _project: str
    ) -> Mapping[str, JsonValue]:
        self.calls.append("project")
        return {"id": 42}

    def list_open_merge_requests(
        self,
        _credential: GitLabCredentialContext,
        _project_id: int,
        _source: str,
        _target: str,
    ) -> tuple[Mapping[str, JsonValue], ...]:
        self.calls.append("lookup")
        return self.matches

    def upload_project_file(
        self,
        _credential: GitLabCredentialContext,
        _project_id: int,
        _path: Path,
        _content_type: str,
    ) -> Mapping[str, JsonValue]:
        self.calls.append("upload")
        return {"markdown": self.upload_markdown}

    def create_merge_request(
        self,
        _credential: GitLabCredentialContext,
        _project_id: int,
        *,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str,
    ) -> Mapping[str, JsonValue]:
        self.calls.append("create")
        self.mutation_descriptions.append(description)
        if self.mutation_error is not None:
            raise self.mutation_error
        return {"iid": 7, "web_url": "https://gitlab.example/team/repo/-/merge_requests/7"}

    def update_merge_request(
        self,
        _credential: GitLabCredentialContext,
        _project_id: int,
        _iid: int,
        *,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str,
    ) -> Mapping[str, JsonValue]:
        self.calls.append("update")
        self.mutation_descriptions.append(description)
        if self.mutation_error is not None:
            raise self.mutation_error
        return {"iid": 7, "web_url": "https://gitlab.example/team/repo/-/merge_requests/7"}


def test_merge_request_uploads_once_and_never_stages_image(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _repo(root)
    description = tmp_path / "description.md"
    description.write_text("User-authored body\n", encoding="utf-8")
    image = tmp_path / "diagram.png"
    image.write_bytes(b"png bytes")
    credential, identity = _identity()
    transport = _Transport()

    command = _resource(root).merge_request_command(
        repository=root,
        member_id=MEMBER,
        target="main",
        title="Add feature",
        description_file=description,
        image=image,
        credential=credential,
        identity=identity,
        publication_transport=transport,
    )

    assert [step.step_id for step in command.plan.steps] == [
        "gitlab.mr.lookup",
        "gitlab.mr.upload-image",
        "gitlab.mr.mutate",
    ]
    result = command.run()
    assert result.outcome == "created"
    assert transport.calls == ["project", "lookup", "upload", "create"]
    assert "diagram.png" not in _git(root, "status", "--short")


def test_merge_request_same_digest_reuses_owned_link(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _repo(root)
    description = tmp_path / "description.md"
    description.write_text("body\n", encoding="utf-8")
    image = tmp_path / "diagram.png"
    image.write_bytes(b"png bytes")
    digest = __import__("hashlib").sha256(b"png bytes").hexdigest()
    existing: Mapping[str, JsonValue] = {
        "iid": 7,
        "description": f"old\n\n<!-- odcli:image sha256={digest} -->\nold-link\n<!-- /odcli:image -->",
    }
    credential, identity = _identity()
    transport = _Transport((existing,))

    result = _resource(root).merge_request(
        repository=root,
        member_id=MEMBER,
        target="main",
        title="Update feature",
        description_file=description,
        image=image,
        credential=credential,
        identity=identity,
        publication_transport=transport,
    )

    assert result.outcome == "updated"
    assert "upload" not in transport.calls
    assert transport.calls[-1] == "update"


def test_merge_request_changed_digest_replaces_owned_block_and_updates_once(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _repo(root)
    description = tmp_path / "description.md"
    old_digest = "a" * 64
    description.write_text(
        "User-authored preface\n\n"
        "![user image](/uploads/user.png)\n\n"
        f"<!-- odcli:image sha256={old_digest} -->\n"
        "![old image](/uploads/old.png)\n"
        "<!-- /odcli:image -->\n\n"
        "User-authored tail\n",
        encoding="utf-8",
    )
    image = tmp_path / "diagram.png"
    image.write_bytes(b"new png bytes")
    new_digest = hashlib.sha256(b"new png bytes").hexdigest()
    existing: Mapping[str, JsonValue] = {
        "iid": 7,
        "description": f"existing\n\n<!-- odcli:image sha256={old_digest} -->\n"
        "![old image](/uploads/old.png)\n<!-- /odcli:image -->",
    }
    credential, identity = _identity()
    transport = _Transport((existing,), upload_markdown="![new image](/uploads/new.png)")

    result = _resource(root).merge_request(
        repository=root,
        member_id=MEMBER,
        target="main",
        title="Update feature",
        description_file=description,
        image=image,
        credential=credential,
        identity=identity,
        publication_transport=transport,
    )

    assert result.outcome == "updated"
    assert transport.calls == ["project", "lookup", "upload", "update"]
    assert len(transport.mutation_descriptions) == 1
    updated_description = transport.mutation_descriptions[0]
    assert "User-authored preface" in updated_description
    assert "![user image](/uploads/user.png)" in updated_description
    assert "User-authored tail" in updated_description
    assert "old image" not in updated_description
    assert updated_description.count("![new image](/uploads/new.png)") == 1
    assert updated_description.count("<!-- odcli:image sha256=") == 1
    assert f"sha256={new_digest}" in updated_description


def test_merge_request_upload_failure_reports_possible_orphan_without_retry(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _repo(root)
    description = tmp_path / "description.md"
    description.write_text("User-authored body\n", encoding="utf-8")
    image = tmp_path / "diagram.png"
    image.write_bytes(b"png bytes")
    credential, identity = _identity()
    transport = _Transport(mutation_error=RuntimeError("private-token should not escape"))
    command = _resource(root).merge_request_command(
        repository=root,
        member_id=MEMBER,
        target="main",
        title="Create feature",
        description_file=description,
        image=image,
        credential=credential,
        identity=identity,
        publication_transport=transport,
    )

    with pytest.raises(GitLabPublicationError, match="possible orphan upload") as error:
        command.run()

    assert str(error.value) == (
        "merge-request mutation failed after image upload; possible orphan upload"
    )
    assert "private-token" not in str(error.value)
    assert transport.calls == ["project", "lookup", "upload", "create"]


def test_merge_request_ambiguity_precedes_image_upload(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _repo(root)
    description = tmp_path / "description.md"
    description.write_text("body\n", encoding="utf-8")
    image = tmp_path / "diagram.png"
    image.write_bytes(b"png bytes")
    credential, identity = _identity()
    transport = _Transport(({"iid": 1}, {"iid": 2}))
    command = _resource(root).merge_request_command(
        repository=root,
        member_id=MEMBER,
        target="main",
        title="Ambiguous",
        description_file=description,
        image=image,
        credential=credential,
        identity=identity,
        publication_transport=transport,
    )

    with pytest.raises(GitLabPublicationError, match="multiple"):
        command.run()
    assert transport.calls == ["project", "lookup"]
