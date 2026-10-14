from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import msgspec
import pytest

from odoo_instance_sdk.exceptions import (
    GitLabCredentialError,
    GitLabIdentityError,
    GitRepositoryChangedError,
)
from odoo_instance_sdk.internal.git_repository import resolve_repository, revalidate_repository
from odoo_instance_sdk.internal.gitlab_credentials import (
    cleanup_askpass_channel,
    ensure_credential_directory,
    read_gitlab_credential_mappings,
    resolve_gitlab_credential,
    verify_git_transport,
    write_askpass_channel,
)
from odoo_instance_sdk.internal.gitlab_identity import verify_gitlab_identity
from odoo_instance_sdk.internal.proc import SubprocessExecutor, prepared_step
from odoo_instance_sdk.internal.proc.redaction import project_process_step
from odoo_instance_sdk.internal.transport.gitlab import GitLabHttpClient
from odoo_instance_sdk.models import GitMergeRequestResult
from odoo_instance_sdk.resources.git import _command_result

MEMBER_A = "11111111-1111-4111-8111-111111111111"
MEMBER_B = "22222222-2222-4222-8222-222222222222"


def _credentials(root: Path, *, duplicate: bool = False) -> None:
    directory = ensure_credential_directory(root)
    mapping = (
        "[[mappings]]\n"
        f'user_id = "{MEMBER_A}"\n'
        'host = "https://GitLab.Example/"\n'
        'login = "alice"\n'
        'token_key = "ODCLI_GITLAB_TOKEN_ALICE"\n'
    )
    if duplicate:
        mapping += (
            "\n[[mappings]]\n"
            f'user_id = "{MEMBER_A}"\n'
            'host = "gitlab.example"\n'
            'login = "alice-2"\n'
            'token_key = "ODCLI_GITLAB_TOKEN_ALICE_2"\n'
        )
    mapping_path = directory / "gitlab-credentials.toml"
    mapping_path.write_text(mapping, encoding="utf-8")
    mapping_path.chmod(0o600)
    env_path = directory / ".env"
    env_path.write_text(
        "ODCLI_GITLAB_TOKEN_ALICE=file-token\nODCLI_GITLAB_TOKEN_ALICE_2=other-token\n",
        encoding="utf-8",
    )
    env_path.chmod(0o600)


def test_global_credential_resolution_is_owner_only_and_process_precedence(
    tmp_path: Path,
) -> None:
    _credentials(tmp_path)

    credential = resolve_gitlab_credential(
        MEMBER_A,
        "gitlab.example",
        user_root=tmp_path,
        process_environment={"ODCLI_GITLAB_TOKEN_ALICE": "process-token"},
    )

    assert credential.identity.host == "gitlab.example"
    assert credential.child_environment()["GITLAB_TOKEN"] == "process-token"
    assert "process-token" not in repr(credential)
    assert "process-token" not in repr(credential.identity)


def test_credential_mapping_rejects_duplicate_and_unprotected_files(tmp_path: Path) -> None:
    _credentials(tmp_path, duplicate=True)
    with pytest.raises(GitLabCredentialError, match="ambiguous"):
        read_gitlab_credential_mappings(user_root=tmp_path)

    mapping = tmp_path / "credentials" / "gitlab-credentials.toml"
    mapping.chmod(0o644)
    with pytest.raises(GitLabCredentialError, match="owner-only"):
        read_gitlab_credential_mappings(user_root=tmp_path)


def test_credential_mapping_requires_canonical_uuid_and_rejects_duplicate_uuid(
    tmp_path: Path,
) -> None:
    directory = ensure_credential_directory(tmp_path)
    mapping = directory / "gitlab-credentials.toml"
    mapping.write_text(
        "[[mappings]]\n"
        'user_id = "11111111-1111-4111-8111-111111111111"\n'
        'host = "gitlab.example"\nlogin = "alice"\n'
        'token_key = "ODCLI_GITLAB_TOKEN_ALICE"\n',
        encoding="utf-8",
    )
    mapping.chmod(0o600)

    parsed = read_gitlab_credential_mappings(user_root=tmp_path)
    assert parsed[0].user_id == MEMBER_A

    mapping.write_text(
        "[[mappings]]\n"
        'user_id = "not-a-uuid"\n'
        'host = "gitlab.example"\nlogin = "alice"\n'
        'token_key = "ODCLI_GITLAB_TOKEN_ALICE"\n',
        encoding="utf-8",
    )
    with pytest.raises(GitLabCredentialError, match="member ID must be a UUID"):
        read_gitlab_credential_mappings(user_root=tmp_path)

    mapping.write_text(
        "[[mappings]]\n"
        'user_id = "11111111-1111-4111-8111-111111111111"\n'
        'host = "gitlab.example"\nlogin = "alice"\n'
        'token_key = "ODCLI_GITLAB_TOKEN_ALICE"\n\n'
        "[[mappings]]\n"
        'user_id = "11111111-1111-4111-8111-111111111111"\n'
        'host = "https://GITLAB.EXAMPLE/"\nlogin = "alice-2"\n'
        'token_key = "ODCLI_GITLAB_TOKEN_ALICE_2"\n',
        encoding="utf-8",
    )
    with pytest.raises(GitLabCredentialError, match="ambiguous"):
        read_gitlab_credential_mappings(user_root=tmp_path)


def test_credential_reads_reject_foreign_owner_and_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _credentials(tmp_path)
    directory = tmp_path / "credentials"
    mapping = directory / "gitlab-credentials.toml"

    current_uid = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: current_uid + 1)
    with pytest.raises(GitLabCredentialError, match="owned"):
        read_gitlab_credential_mappings(user_root=tmp_path)

    monkeypatch.undo()
    target = directory / "target.toml"
    target.write_text(mapping.read_text(encoding="utf-8"), encoding="utf-8")
    target.chmod(0o600)
    mapping.unlink()
    mapping.symlink_to(target)
    with pytest.raises(GitLabCredentialError, match="regular owner-only"):
        read_gitlab_credential_mappings(user_root=tmp_path)


def test_two_member_snapshots_do_not_share_private_tokens(tmp_path: Path) -> None:
    directory = ensure_credential_directory(tmp_path)
    (directory / "gitlab-credentials.toml").write_text(
        "[[mappings]]\n"
        f'user_id = "{MEMBER_A}"\nhost = "gitlab.example"\nlogin = "alice"\n'
        'token_key = "ODCLI_GITLAB_TOKEN_A"\n\n'
        "[[mappings]]\n"
        f'user_id = "{MEMBER_B}"\nhost = "gitlab.example"\nlogin = "bob"\n'
        'token_key = "ODCLI_GITLAB_TOKEN_B"\n',
        encoding="utf-8",
    )
    (directory / "gitlab-credentials.toml").chmod(0o600)
    (directory / ".env").write_text(
        "ODCLI_GITLAB_TOKEN_A=token-a\nODCLI_GITLAB_TOKEN_B=token-b\n", encoding="utf-8"
    )
    (directory / ".env").chmod(0o600)

    first = resolve_gitlab_credential(MEMBER_A, "gitlab.example", user_root=tmp_path)
    second = resolve_gitlab_credential(MEMBER_B, "gitlab.example", user_root=tmp_path)

    assert first.child_environment()["GITLAB_TOKEN"] == "token-a"
    assert second.child_environment()["GITLAB_TOKEN"] == "token-b"
    assert first.login != second.login

    channel = write_askpass_channel(first)
    try:
        assert Path(channel).stat().st_mode & 0o077 == 0
        assert "token-a" in Path(channel).read_text(encoding="utf-8")
        environment = first.askpass_environment("askpass", channel_path=channel)
        assert "GITLAB_TOKEN" not in environment
    finally:
        cleanup_askpass_channel(channel)
    assert not Path(channel).exists()


def test_gitlab_identity_verification_requires_owner_and_commit_identity(tmp_path: Path) -> None:
    _credentials(tmp_path)
    credential = resolve_gitlab_credential(MEMBER_A, "gitlab.example", user_root=tmp_path)

    verified = verify_gitlab_identity(
        credential,
        lambda _host, token: {
            "id": 7,
            "username": "alice",
            "name": "Alice Example",
            "commit_email": "alice@example.test",
            "token_seen_by_fake": token,
        },
    )
    assert verified.user_id == 7
    assert verified.email == "alice@example.test"

    with pytest.raises(GitLabIdentityError, match="does not match"):
        verify_gitlab_identity(
            credential,
            lambda _host, _token: {
                "id": 8,
                "username": "mallory",
                "name": "Mallory",
                "email": "m@example.test",
            },
        )
    with pytest.raises(GitLabIdentityError, match="commit name or email"):
        verify_gitlab_identity(
            credential,
            lambda _host, _token: {"id": 7, "username": "alice", "name": "Alice"},
        )


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)


def test_repository_snapshot_is_canonical_and_revalidation_is_immutable(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "feature", str(root)], check=True)
    _git(root, "remote", "add", "origin", "https://GitLab.Example/team/repo.git")
    _git(root, "config", "user.email", "test@example.test")
    _git(root, "config", "user.name", "Test")
    (root / "README.md").write_text("foundation\n", encoding="utf-8")
    _git(root, "add", "README.md")
    (root / ".odcli").mkdir()
    (root / ".odcli" / "project.toml").write_text("[project]\n", encoding="utf-8")

    snapshot = resolve_repository(root)

    assert snapshot.canonical_root == str(root.resolve())
    assert snapshot.git_common_dir == str((root / ".git").resolve())
    assert snapshot.origin == "https://gitlab.example/team/repo"
    assert snapshot.host == "gitlab.example"
    assert snapshot.project == "team/repo"
    assert snapshot.branch == "feature"
    assert snapshot.staged_paths == ("README.md",)
    assert snapshot.worktree_dirty
    assert snapshot.initialized_odoo
    assert revalidate_repository(snapshot) == snapshot

    _git(root, "remote", "set-url", "origin", "https://gitlab.example/team/changed.git")
    with pytest.raises(GitRepositoryChangedError, match="changed"):
        revalidate_repository(snapshot)


def test_transport_preflight_rejects_overrides_and_foreign_https_hosts(tmp_path: Path) -> None:
    with pytest.raises(GitLabCredentialError, match="overrides"):
        verify_git_transport(
            tmp_path,
            ("-c", "credential.helper=store", "fetch"),
            "https://gitlab.example/team/repo.git",
        )
    with pytest.raises(GitLabCredentialError, match="ambiguous"):
        verify_git_transport(
            tmp_path,
            ("clone", "https://other.example/team/repo.git"),
            "https://gitlab.example/team/repo.git",
        )


def test_merge_request_result_serializes_project_and_exports_public_shape() -> None:
    result = GitMergeRequestResult(
        project="team/repo",
        merge_request_id=7,
        web_url="https://gitlab.example/team/repo/-/merge_requests/7",
        source_branch="feature",
        target_branch="main",
        outcome="created",
    )

    assert msgspec.to_builtins(result) == {
        "project": "team/repo",
        "merge_request_id": 7,
        "web_url": "https://gitlab.example/team/repo/-/merge_requests/7",
        "source_branch": "feature",
        "target_branch": "main",
        "outcome": "created",
    }
    assert msgspec.json.decode(msgspec.json.encode(result), type=GitMergeRequestResult) == result


def test_credential_secret_is_redacted_from_child_projection_and_gitlab_failure() -> None:
    secret = "gitlab-token-secret"
    step = prepared_step(
        sys.executable,
        (
            "-c",
            (
                "import sys; print('stdout=' + sys.argv[1]); "
                "print('stderr=' + sys.argv[1], file=sys.stderr); raise SystemExit(3)"
            ),
            secret,
        ),
        step_id="gitlab.child",
        secret_values=(secret,),
    )
    projection = project_process_step(step)
    assert secret not in repr(projection)
    assert secret not in str(projection)
    process = SubprocessExecutor().execute(step)
    result = _command_result(process, step)
    assert result.returncode == 3
    assert secret not in result.stdout
    assert secret not in result.stderr

    class FailingClient:
        def get(self, _url: str, **_kwargs: object) -> object:
            raise RuntimeError(f"authorization={secret}")

    client = GitLabHttpClient()
    client._client = FailingClient()  # type: ignore[assignment]
    with pytest.raises(GitLabIdentityError) as error:
        client.get_user("gitlab.example", secret)
    assert secret not in repr(error.value)
    assert secret not in str(error.value)
