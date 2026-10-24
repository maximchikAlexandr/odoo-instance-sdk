from __future__ import annotations

import hashlib
import os
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from odoo_instance_sdk.client import OdooClient
from odoo_instance_sdk.config import InstanceConfig, OdooClientConfig
from odoo_instance_sdk.exceptions import GitLabCredentialError, GitLabPublicationError
from odoo_instance_sdk.execution import JsonValue
from odoo_instance_sdk.internal.secret_store import SecretStore
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


def _legacy_credentials(root: Path) -> tuple[Path, Path]:
    directory = root / "credentials"
    directory.mkdir(mode=0o700)
    mapping = directory / "gitlab-credentials.toml"
    mapping.write_text(
        "[[mappings]]\n"
        f'user_id = "{MEMBER}"\n'
        'host = "gitlab.example"\n'
        'login = "alice"\n'
        'token_key = "ODCLI_GITLAB_TOKEN_ALICE"\n',
        encoding="utf-8",
    )
    mapping.chmod(0o600)
    environment = directory / ".env"
    environment.write_text("ODCLI_GITLAB_TOKEN_ALICE=file-token\n", encoding="utf-8")
    environment.chmod(0o600)
    return mapping, environment


def _identity_callback(tokens: list[str]) -> Callable[[str, str], Mapping[str, JsonValue]]:
    def verify(host: str, token: str) -> Mapping[str, JsonValue]:
        assert host == "gitlab.example"
        tokens.append(token)
        return {
            "id": 7,
            "username": "alice",
            "name": "Alice Example",
            "email": "alice@example.test",
        }

    return verify


def _prepare_store_hit(store: SecretStore) -> None:
    store.create_gitlab_member(MEMBER, "gitlab.example", "alice", "encrypted-token")


def _prepare_store_absent(_store: SecretStore) -> None:
    pass


def _prepare_store_broken(store: SecretStore) -> None:
    _prepare_store_hit(store)
    store.key_path.unlink()


def _prepare_store_invalid(store: SecretStore) -> None:
    _prepare_store_hit(store)
    driver = __import__("sqlcipher3.dbapi2", fromlist=["connect"])
    connection = driver.connect(str(store.store_path))
    connection.execute(f"PRAGMA key = \"x'{store.key_path.read_bytes().hex()}'\"")
    connection.execute("UPDATE gitlab_member_secret SET token = ''")
    connection.commit()
    connection.close()


@pytest.mark.parametrize(
    ("prepare_store", "expected"),
    [
        (_prepare_store_hit, "encrypted-token"),
        (_prepare_store_absent, "process-token"),
    ],
    ids=["encrypted-hit", "exact-absence"],
)
@pytest.mark.dashboard
def test_public_merge_request_resolves_store_before_legacy_without_leaking_secrets(
    tmp_path: Path,
    prepare_store: Callable[[SecretStore], None],
    expected: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    root = tmp_path / "repo"
    _repo(root)
    description = tmp_path / "description.md"
    description.write_text("body\n", encoding="utf-8")
    store_root = tmp_path / "store"
    store = SecretStore(user_root=store_root)
    mapping, environment = _legacy_credentials(tmp_path)
    prepare_store(store)
    identity_tokens: list[str] = []
    transport = _Transport()

    command = _resource(root).merge_request_command(
        repository=root,
        member_id=MEMBER,
        target="main",
        title="Create feature",
        description_file=description,
        user_root=store_root,
        mapping_path=mapping,
        environment_path=environment,
        process_environment={"ODCLI_GITLAB_TOKEN_ALICE": "process-token"},
        identity_transport=_identity_callback(identity_tokens),
        publication_transport=transport,
    )
    assert "encrypted-token" not in repr(command.plan)
    assert "process-token" not in repr(command.plan)
    result = command.run()

    assert identity_tokens == [expected]
    assert transport.tokens == [expected]
    assert expected not in repr(result)
    assert "database" not in repr(result).casefold()
    assert expected not in caplog.text


@pytest.mark.parametrize(
    "prepare_store",
    [_prepare_store_broken, _prepare_store_invalid],
    ids=["broken-store", "invalid-record"],
)
@pytest.mark.dashboard
def test_public_merge_request_fails_closed_for_store_errors_without_legacy_fallback(
    tmp_path: Path,
    prepare_store: Callable[[SecretStore], None],
    caplog: pytest.LogCaptureFixture,
) -> None:
    root = tmp_path / "repo"
    _repo(root)
    description = tmp_path / "description.md"
    description.write_text("body\n", encoding="utf-8")
    store_root = tmp_path / "store"
    store = SecretStore(user_root=store_root)
    prepare_store(store)
    mapping, environment = _legacy_credentials(tmp_path)
    identity_tokens: list[str] = []
    transport = _Transport()

    with pytest.raises(GitLabCredentialError) as failure:
        _resource(root).merge_request_command(
            repository=root,
            member_id=MEMBER,
            target="main",
            title="Create feature",
            description_file=description,
            user_root=store_root,
            mapping_path=mapping,
            environment_path=environment,
            process_environment={"ODCLI_GITLAB_TOKEN_ALICE": "legacy-token"},
            identity_transport=_identity_callback(identity_tokens),
            publication_transport=transport,
        )

    text = repr(failure.value)
    assert "encrypted-token" not in text
    assert "legacy-token" not in text
    assert "database" not in text.casefold()
    assert identity_tokens == []
    assert transport.calls == []
    assert "encrypted-token" not in caplog.text
    assert "legacy-token" not in caplog.text


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
        self.tokens: list[str] = []
        self.mutation_descriptions: list[str] = []
        self.uploaded_contents: list[bytes] = []

    def get_project(
        self, _credential: GitLabCredentialContext, _project: str
    ) -> Mapping[str, JsonValue]:
        self.calls.append("project")
        self.tokens.append(_credential._token)
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
        _filename: str,
        _content: bytes,
        _content_type: str,
    ) -> Mapping[str, JsonValue]:
        self.calls.append("upload")
        self.uploaded_contents.append(_content)
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
    assert list(tmp_path.glob("odcli-image-*")) == []
    replacement = tmp_path / "replacement.md"
    replacement.write_text("swapped body\n", encoding="utf-8")
    os.replace(replacement, description)
    image.unlink()
    image.write_bytes(b"replacement png bytes")
    result = command.run()
    assert result.outcome == "created"
    assert transport.calls == ["project", "lookup", "upload", "create"]
    assert transport.uploaded_contents == [b"png bytes"]
    assert transport.mutation_descriptions[0].startswith("User-authored body\n")
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

    target = tmp_path / "description-target.md"
    target.write_text("body\n", encoding="utf-8")
    description.unlink()
    description.symlink_to(target)
    with pytest.raises(GitLabPublicationError, match="unavailable"):
        _resource(root).merge_request_command(
            repository=root,
            member_id=MEMBER,
            target="main",
            title="No symlink",
            description_file=description,
            credential=credential,
            identity=identity,
            publication_transport=_Transport(),
        )

    directory = tmp_path / "description-dir"
    directory.mkdir()
    with pytest.raises(GitLabPublicationError, match="must be regular"):
        _resource(root).merge_request_command(
            repository=root,
            member_id=MEMBER,
            target="main",
            title="Directory",
            description_file=directory,
            credential=credential,
            identity=identity,
            publication_transport=_Transport(),
        )

    invalid = tmp_path / "invalid.md"
    invalid.write_bytes(b"\xff")
    with pytest.raises(GitLabPublicationError, match="valid UTF-8"):
        _resource(root).merge_request_command(
            repository=root,
            member_id=MEMBER,
            target="main",
            title="Invalid UTF-8",
            description_file=invalid,
            credential=credential,
            identity=identity,
            publication_transport=_Transport(),
        )

    oversized = tmp_path / "oversized.md"
    oversized.write_bytes(b"x" * (1_048_576 + 1))
    with pytest.raises(GitLabPublicationError, match="too large"):
        _resource(root).merge_request_command(
            repository=root,
            member_id=MEMBER,
            target="main",
            title="Oversized",
            description_file=oversized,
            credential=credential,
            identity=identity,
            publication_transport=_Transport(),
        )
