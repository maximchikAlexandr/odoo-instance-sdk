from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.exceptions import GitLabPublicationError
from odoo_instance_sdk.models import (
    GitLabCredentialContext,
    GitLabCredentialIdentity,
    GitLabIdentity,
)
from odoo_instance_sdk.resources.git import GitResource

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
    return GitResource(
        SimpleNamespace(config=InstanceConfig(base_url="http://127.0.0.1:8069", default_cwd=root))
    )  # type: ignore[arg-type]


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


class _Transport:
    def __init__(self, matches: tuple[Mapping[str, Any], ...] = ()) -> None:
        self.matches = matches
        self.calls: list[str] = []

    def get_project(self, _credential: object, _project: str) -> Mapping[str, Any]:
        self.calls.append("project")
        return {"id": 42}

    def list_open_merge_requests(
        self, _credential: object, _project_id: int, _source: str, _target: str
    ) -> tuple[Mapping[str, Any], ...]:
        self.calls.append("lookup")
        return self.matches

    def upload_project_file(
        self, _credential: object, _project_id: int, _path: Path, _content_type: str
    ) -> Mapping[str, Any]:
        self.calls.append("upload")
        return {"markdown": "![image](/uploads/image.png)"}

    def create_merge_request(
        self, _credential: object, _project_id: int, **kwargs: Any
    ) -> Mapping[str, Any]:
        self.calls.append("create")
        return {"iid": 7, "web_url": "https://gitlab.example/team/repo/-/merge_requests/7"}

    def update_merge_request(
        self, _credential: object, _project_id: int, _iid: int, **kwargs: Any
    ) -> Mapping[str, Any]:
        self.calls.append("update")
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
    existing = {
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
