from __future__ import annotations

import subprocess
from pathlib import Path

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
