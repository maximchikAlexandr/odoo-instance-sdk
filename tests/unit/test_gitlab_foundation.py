from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import msgspec
import pytest

from odoo_instance_sdk.exceptions import (
    GitLabCredentialError,
    GitLabIdentityError,
    GitRepositoryChangedError,
    GitRepositoryError,
)
from odoo_instance_sdk.internal.git_frontier import frontier_environment
from odoo_instance_sdk.internal.git_repository import (
    resolve_repository,
    revalidate_repository,
    validate_submodule_configuration,
)
from odoo_instance_sdk.internal.gitlab_credentials import (
    ensure_credential_directory,
    ensure_packaged_askpass,
    read_gitlab_credential_mappings,
    resolve_gitlab_credential,
    verify_git_transport,
)
from odoo_instance_sdk.internal.gitlab_identity import verify_gitlab_identity
from odoo_instance_sdk.internal.proc import (
    PreparedStep,
    ProcessOutputLimitError,
    StepEvent,
    SubprocessExecutor,
    prepared_step,
)
from odoo_instance_sdk.internal.proc.redaction import project_process_step
from odoo_instance_sdk.internal.project_env import ProjectEnvironmentError, load_environment_file
from odoo_instance_sdk.internal.secret_store import SecretStore
from odoo_instance_sdk.internal.transport.gitlab import GitLabHttpClient
from odoo_instance_sdk.models import GitLabCredentialContext, GitMergeRequestResult
from odoo_instance_sdk.resources.git import _command_result

MEMBER_A = "11111111-1111-4111-8111-111111111111"
MEMBER_B = "22222222-2222-4222-8222-222222222222"


def test_packaged_askpass_replaces_stale_uv_script_atomically(tmp_path: Path) -> None:
    helper_dir = tmp_path / "bin"
    helper_dir.mkdir()
    helper = helper_dir / "odcli-git-askpass"
    helper.write_text("#!/tmp/staging/python\n", encoding="utf-8")
    helper.chmod(0o700)

    ensure_packaged_askpass(helper)
    content = helper.read_text(encoding="utf-8")
    assert content.startswith(f"#!{Path(sys.executable).absolute()}\n")
    assert "git_askpass import main" in content
    assert helper.stat().st_mode & 0o077 == 0
    ensure_packaged_askpass(helper)
    assert helper.read_text(encoding="utf-8") == content

    unsafe = tmp_path / "unsafe-askpass"
    interpreter = Path(sys.executable).absolute()
    unsafe.write_text(
        f"#!{interpreter}-attacker\n#!{interpreter} still not an exact launcher\n",
        encoding="utf-8",
    )
    unsafe.chmod(0o644)
    ensure_packaged_askpass(unsafe)
    expected = (
        f"#!{interpreter}\n"
        "from odoo_instance_sdk.internal.git_askpass import main\n"
        "raise SystemExit(main())\n"
    )
    assert unsafe.read_text(encoding="utf-8") == expected
    assert unsafe.stat().st_uid == os.getuid()
    assert unsafe.stat().st_mode & 0o777 == 0o700

    target = tmp_path / "askpass-target"
    target.write_text("stale", encoding="utf-8")
    symlink = tmp_path / "symlink-askpass"
    symlink.symlink_to(target)
    ensure_packaged_askpass(symlink)
    assert symlink.is_file() and not symlink.is_symlink()
    assert target.read_text(encoding="utf-8") == "stale"

    ensure_packaged_askpass(tmp_path / "missing" / "odcli-git-askpass")


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


def test_gitlab_unified_user_dotenv_and_project_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(SecretStore, "lookup_gitlab_member", lambda *_args: None)
    credentials = ensure_credential_directory(tmp_path)
    mapping = credentials / "gitlab-credentials.toml"
    mapping.write_text(
        f'[[mappings]]\nuser_id = "{MEMBER_A}"\nhost = "gitlab.example"\n'
        'login = "alice"\ntoken_key = "ODCLI_GITLAB_TOKEN_ALICE"\n',
        encoding="utf-8",
    )
    mapping.chmod(0o600)
    user_env = tmp_path / ".env"
    user_env.write_text("ODCLI_GITLAB_TOKEN_ALICE=user-token\n", encoding="utf-8")
    user_env.chmod(0o600)
    project = tmp_path / "project"
    project_env = project / ".odcli" / ".env"
    project_env.parent.mkdir(parents=True)
    project_env.write_text("ODCLI_GITLAB_TOKEN_ALICE=project-token\n", encoding="utf-8")
    project_env.chmod(0o600)

    credential = resolve_gitlab_credential(
        MEMBER_A,
        "gitlab.example",
        user_root=tmp_path,
        project_root=project,
        process_environment={},
    )
    assert credential.child_environment()["GITLAB_TOKEN"] == "project-token"
    credential = resolve_gitlab_credential(
        MEMBER_A,
        "gitlab.example",
        user_root=tmp_path,
        project_root=None,
        process_environment={},
    )
    assert credential.child_environment()["GITLAB_TOKEN"] == "user-token"


@pytest.mark.parametrize("explicit", [False, True], ids=["project", "explicit-legacy"])
def test_public_credential_resolver_rejects_dotenv_path_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, explicit: bool
) -> None:
    monkeypatch.setattr(SecretStore, "lookup_gitlab_member", lambda *_args: None)
    _credentials(tmp_path)
    project = tmp_path / "project"
    project_env = project / ".odcli" / ".env"
    project_env.parent.mkdir(parents=True)
    project_env.write_text("ODCLI_GITLAB_TOKEN_ALICE=project-token\n", encoding="utf-8")
    project_env.chmod(0o600)
    explicit_env = tmp_path / "explicit.env"
    explicit_env.write_text("ODCLI_GITLAB_TOKEN_ALICE=explicit-token\n", encoding="utf-8")
    explicit_env.chmod(0o600)
    attacker = tmp_path / "attacker.env"
    attacker.write_text("ODCLI_GITLAB_TOKEN_ALICE=attacker-token\n", encoding="utf-8")
    attacker.chmod(0o600)
    target = explicit_env if explicit else project_env
    original_read_bytes = Path.read_bytes

    def swap_before_pathname_read(path: Path) -> bytes:
        if path == target:
            path.unlink()
            path.symlink_to(attacker)
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", swap_before_pathname_read)
    credential = resolve_gitlab_credential(
        MEMBER_A,
        "gitlab.example",
        user_root=tmp_path,
        project_root=project,
        environment_path=explicit_env if explicit else None,
        process_environment={},
    )

    assert credential.child_environment()["GITLAB_TOKEN"] == (
        "explicit-token" if explicit else "project-token"
    )


@pytest.mark.parametrize("explicit", [False, True], ids=["project", "explicit-legacy"])
def test_public_credential_resolver_fails_closed_on_dotenv_symlink_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, explicit: bool
) -> None:
    monkeypatch.setattr(SecretStore, "lookup_gitlab_member", lambda *_args: None)
    _credentials(tmp_path)
    project = tmp_path / "project"
    project_env = project / ".odcli" / ".env"
    project_env.parent.mkdir(parents=True)
    project_env.write_text("ODCLI_GITLAB_TOKEN_ALICE=project-token\n", encoding="utf-8")
    project_env.chmod(0o600)
    explicit_env = tmp_path / "explicit.env"
    explicit_env.write_text("ODCLI_GITLAB_TOKEN_ALICE=explicit-token\n", encoding="utf-8")
    explicit_env.chmod(0o600)
    attacker = tmp_path / "attacker.env"
    attacker.write_text("ODCLI_GITLAB_TOKEN_ALICE=attacker-token\n", encoding="utf-8")
    attacker.chmod(0o600)
    target = explicit_env if explicit else project_env
    original_open = os.open
    swapped = False

    def swap_before_descriptor_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        if path == target.name and dir_fd is not None and not swapped:
            target.unlink()
            target.symlink_to(attacker)
            swapped = True
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", swap_before_descriptor_open)
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {swap_before_descriptor_open})
    with pytest.raises(GitLabCredentialError) as failure:
        resolve_gitlab_credential(
            MEMBER_A,
            "gitlab.example",
            user_root=tmp_path,
            project_root=project,
            environment_path=explicit_env if explicit else None,
            process_environment={},
        )

    assert swapped
    assert "attacker-token" not in str(failure.value)


@pytest.mark.parametrize("explicit", [False, True], ids=["project", "explicit-legacy"])
def test_dotenv_loader_detects_inode_swap_without_nofollow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, explicit: bool
) -> None:
    project = tmp_path / "project"
    project_env = project / ".odcli" / ".env"
    project_env.parent.mkdir(parents=True)
    project_env.write_text("ODCLI_GITLAB_TOKEN_ALICE=project-token\n", encoding="utf-8")
    project_env.chmod(0o600)
    explicit_env = tmp_path / "explicit.env"
    explicit_env.write_text("ODCLI_GITLAB_TOKEN_ALICE=explicit-token\n", encoding="utf-8")
    explicit_env.chmod(0o600)
    target = explicit_env if explicit else project_env
    original = target.stat()
    original_identity = original.st_dev, original.st_ino
    displaced = target.with_name(f"{target.name}.displaced")
    original_open = os.open
    swapped = False
    replacement_identity: tuple[int, int] | None = None

    def replace_before_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal replacement_identity, swapped
        if path == target and dir_fd is None and not swapped:
            target.rename(displaced)
            target.write_text("ODCLI_GITLAB_TOKEN_ALICE=attacker-token\n", encoding="utf-8")
            target.chmod(0o600)
            replacement = target.stat()
            replacement_identity = replacement.st_dev, replacement.st_ino
            swapped = True
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "O_NOFOLLOW", 0)
    monkeypatch.setattr(os, "open", replace_before_open)
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd - {original_open})
    with pytest.raises(ProjectEnvironmentError) as failure:
        load_environment_file(target)

    assert swapped
    assert replacement_identity is not None
    assert replacement_identity != original_identity
    assert "attacker-token" not in str(failure.value)


def test_dotenv_loader_sanitizes_open_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    explicit_env = tmp_path / "explicit.env"
    explicit_env.write_text("ODCLI_GITLAB_TOKEN_ALICE=selected-token\n", encoding="utf-8")
    explicit_env.chmod(0o600)
    original_open = os.open

    def fail_file_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if path == explicit_env.name and dir_fd is not None:
            raise PermissionError("private diagnostic")
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", fail_file_open)
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {fail_file_open})
    with pytest.raises(ProjectEnvironmentError) as failure:
        load_environment_file(explicit_env)

    assert "private diagnostic" not in str(failure.value)
    assert "selected-token" not in str(failure.value)


def test_dotenv_loader_rejects_nonregular_file(tmp_path: Path) -> None:
    explicit_env = tmp_path / "explicit.env"
    explicit_env.mkdir(mode=0o700)

    with pytest.raises(ProjectEnvironmentError, match="regular file"):
        load_environment_file(explicit_env)


def test_dotenv_loader_reads_file_without_dir_fd_support(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    explicit_env = tmp_path / "explicit.env"
    explicit_env.write_text("ODCLI_GITLAB_TOKEN_ALICE=selected-token\n", encoding="utf-8")
    explicit_env.chmod(0o600)
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd - {os.open})

    assert load_environment_file(explicit_env) == {"ODCLI_GITLAB_TOKEN_ALICE": "selected-token"}


def _legacy_dotenv(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(SecretStore, "lookup_gitlab_member", lambda *_args: None)
    credentials = ensure_credential_directory(root)
    old_env = credentials / ".env"
    old_env.write_text("ODCLI_GITLAB_TOKEN_ALICE=legacy-token\n", encoding="utf-8")
    old_env.chmod(0o600)
    return old_env


def _resolve_legacy(root: Path, *, environment_path: Path | None = None) -> GitLabCredentialContext:
    return resolve_gitlab_credential(
        MEMBER_A,
        "gitlab.example",
        user_root=root,
        environment_path=environment_path,
        project_root=root / "empty",
        process_environment={},
    )


def test_gitlab_legacy_dotenv_fallback_logs_migration_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _credentials(tmp_path)
    _legacy_dotenv(tmp_path, monkeypatch)
    credential = _resolve_legacy(tmp_path)

    assert "<ODCLI_USER_ROOT>/.env" in caplog.text
    assert credential.child_environment()["GITLAB_TOKEN"] == "legacy-token"


def test_gitlab_legacy_dotenv_conflict_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _credentials(tmp_path)
    _legacy_dotenv(tmp_path, monkeypatch)
    user_env = tmp_path / ".env"
    user_env.write_text("ODCLI_GITLAB_TOKEN_ALICE=unified-token\n", encoding="utf-8")
    user_env.chmod(0o600)
    with pytest.raises(GitLabCredentialError, match="values conflict"):
        _resolve_legacy(tmp_path)


def test_gitlab_legacy_dotenv_equal_values_are_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _credentials(tmp_path)
    old_env = _legacy_dotenv(tmp_path, monkeypatch)
    user_env = tmp_path / ".env"
    user_env.write_text("ODCLI_GITLAB_TOKEN_ALICE=unified-token\n", encoding="utf-8")
    user_env.chmod(0o600)
    old_env.write_text("ODCLI_GITLAB_TOKEN_ALICE=unified-token\n", encoding="utf-8")
    old_env.chmod(0o600)
    credential = _resolve_legacy(tmp_path)

    assert credential.child_environment()["GITLAB_TOKEN"] == "unified-token"
    assert "<ODCLI_USER_ROOT>/.env" in caplog.text


def test_gitlab_explicit_environment_path_overrides_legacy_transition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _credentials(tmp_path)
    _legacy_dotenv(tmp_path, monkeypatch)
    user_env = tmp_path / ".env"
    user_env.write_text("ODCLI_GITLAB_TOKEN_ALICE=conflicting-user-token\n", encoding="utf-8")
    user_env.chmod(0o600)
    explicit = tmp_path / "explicit.env"
    explicit.write_text("ODCLI_GITLAB_TOKEN_ALICE=explicit-token\n", encoding="utf-8")
    explicit.chmod(0o600)
    credential = _resolve_legacy(tmp_path, environment_path=explicit)

    assert credential.child_environment()["GITLAB_TOKEN"] == "explicit-token"


def test_global_legacy_resolution_keeps_owner_only_directory_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(SecretStore, "lookup_gitlab_member", lambda *_args: None)
    credential_dir = tmp_path / ".odcli"
    credential_dir.mkdir()
    credential_dir.chmod(0o755)
    mapping = credential_dir / "gitlab-credentials.toml"
    mapping.write_text(
        f'[[mappings]]\nuser_id = "{MEMBER_A}"\nhost = "gitlab.example"\n'
        'login = "alice"\ntoken_key = "ODCLI_GITLAB_TOKEN_ALICE"\n',
        encoding="utf-8",
    )
    mapping.chmod(0o600)
    environment = credential_dir / ".env"
    environment.write_text("ODCLI_GITLAB_TOKEN_ALICE=file-token\n", encoding="utf-8")
    environment.chmod(0o600)

    with pytest.raises(GitLabCredentialError, match="owner-only"):
        resolve_gitlab_credential(
            MEMBER_A,
            "gitlab.example",
            user_root=tmp_path,
            mapping_path=mapping,
            environment_path=environment,
        )


@pytest.mark.dashboard
def test_encrypted_gitlab_credential_precedes_legacy_mapping(tmp_path: Path) -> None:
    store = SecretStore(user_root=tmp_path)
    created = store.create_gitlab_member(
        MEMBER_A, "https://GitLab.Example/", "encrypted", "secret-token"
    )

    credential = resolve_gitlab_credential(MEMBER_A, "gitlab.example", user_root=tmp_path)

    assert credential.login == "encrypted"
    assert credential.child_environment()["GITLAB_TOKEN"] == "secret-token"
    stored = store.lookup_gitlab_member(MEMBER_A, "gitlab.example")
    assert stored is not None
    assert credential.identity.token_key == stored.token_key
    assert created.login == "encrypted"
    assert "secret-token" not in repr(credential)


@pytest.mark.dashboard
def test_broken_encrypted_gitlab_store_never_falls_back(tmp_path: Path) -> None:
    store = SecretStore(user_root=tmp_path)
    store.create_gitlab_member(MEMBER_A, "gitlab.example", "encrypted", "secret-token")
    store.key_path.unlink()
    _credentials(tmp_path)

    with pytest.raises(GitLabCredentialError, match="encrypted credential lookup failed"):
        resolve_gitlab_credential(MEMBER_A, "gitlab.example", user_root=tmp_path)


@pytest.mark.dashboard
def test_invalid_encrypted_gitlab_record_never_falls_back(tmp_path: Path) -> None:
    store = SecretStore(user_root=tmp_path)
    store.create_gitlab_member(MEMBER_A, "gitlab.example", "encrypted", "secret-token")
    driver = __import__("sqlcipher3.dbapi2", fromlist=["connect"])
    connection = driver.connect(str(store.store_path))
    connection.execute(f"PRAGMA key = \"x'{store.key_path.read_bytes().hex()}'\"")
    connection.execute("UPDATE gitlab_member_secret SET token = ''")
    connection.commit()
    connection.close()
    _credentials(tmp_path)

    with pytest.raises(GitLabCredentialError, match="encrypted credential lookup failed"):
        resolve_gitlab_credential(MEMBER_A, "gitlab.example", user_root=tmp_path)


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


def _two_host_credentials(
    root: Path,
) -> tuple[GitLabCredentialContext, GitLabCredentialContext, GitLabCredentialContext]:
    directory = ensure_credential_directory(root)
    (directory / "gitlab-credentials.toml").write_text(
        "[[mappings]]\n"
        f'user_id = "{MEMBER_A}"\nhost = "gitlab.example"\nlogin = "alice"\n'
        'token_key = "ODCLI_GITLAB_TOKEN_A"\n\n'
        "[[mappings]]\n"
        f'user_id = "{MEMBER_B}"\nhost = "gitlab.example"\nlogin = "bob"\n'
        'token_key = "ODCLI_GITLAB_TOKEN_B"\n'
        "[[mappings]]\n"
        f'user_id = "{MEMBER_A}"\nhost = "gitlab.other.example"\nlogin = "alice-other"\n'
        'token_key = "ODCLI_GITLAB_TOKEN_OTHER"\n',
        encoding="utf-8",
    )
    (directory / "gitlab-credentials.toml").chmod(0o600)
    (directory / ".env").write_text(
        "ODCLI_GITLAB_TOKEN_A=token-a\nODCLI_GITLAB_TOKEN_B=token-b\n"
        "ODCLI_GITLAB_TOKEN_OTHER=token-other\n",
        encoding="utf-8",
    )
    (directory / ".env").chmod(0o600)

    first = resolve_gitlab_credential(MEMBER_A, "gitlab.example", user_root=root)
    second = resolve_gitlab_credential(MEMBER_B, "gitlab.example", user_root=root)
    other_host = resolve_gitlab_credential(MEMBER_A, "gitlab.other.example", user_root=root)
    return first, second, other_host


def test_two_member_snapshots_do_not_share_private_tokens(tmp_path: Path) -> None:
    first, second, _other_host = _two_host_credentials(tmp_path)
    assert first.child_environment()["GITLAB_TOKEN"] == "token-a"
    assert second.child_environment()["GITLAB_TOKEN"] == "token-b"
    assert first.login != second.login


@pytest.mark.parametrize(
    ("host", "expected"),
    (("gitlab.example", "token-a"), ("gitlab.other.example", "token-other")),
)
def test_exact_host_prompt_routes_only_frontier_token(
    tmp_path: Path, host: str, expected: str
) -> None:
    first, _second, other_host = _two_host_credentials(tmp_path)
    frontier = frontier_environment(
        first,
        member_id=MEMBER_A,
        user_root=tmp_path,
        mapping_path=None,
        environment_path=None,
        include_selected=True,
        frontier_credentials=(first, other_host),
    )
    runtime_values, cleanup, _runtime_secrets = frontier["runtime_environment"]()
    channel = dict(runtime_values)["ODCLI_GITLAB_ASKPASS_FILE"]
    try:
        assert Path(channel).stat().st_mode & 0o077 == 0
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "odoo_instance_sdk.internal.git_askpass",
                f"Password for 'https://{host}/team/repo':",
            ],
            env=os.environ | dict(runtime_values),
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == expected
        foreign = subprocess.run(
            [
                sys.executable,
                "-m",
                "odoo_instance_sdk.internal.git_askpass",
                "Password for 'https://foreign.example/team/repo':",
            ],
            env=os.environ | dict(runtime_values),
            capture_output=True,
            text=True,
            check=False,
        )
        assert foreign.returncode != 0
        assert foreign.stdout == ""
    finally:
        cleanup()
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

    root = tmp_path / "repository"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "remote", "add", "origin", "https://gitlab.example/team/repo"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "--local", "http.sslCAInfo", "/tmp/ca.pem"],
        check=True,
    )
    with pytest.raises(GitRepositoryError, match="unsafe"):
        validate_submodule_configuration(root, allowed_hosts=("gitlab.example",))


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

    first_host_token = "first-host-runtime-token"
    second_host_token = "second-host-runtime-token"

    def runtime_environment() -> tuple[
        tuple[tuple[str, str], ...], Callable[[], None], tuple[str, ...]
    ]:
        return (), lambda: None, (first_host_token, second_host_token)

    runtime_step = PreparedStep(
        step_id="gitlab.runtime-child",
        argv=(
            sys.executable,
            "-c",
            "import sys; print(sys.argv[1]); print(sys.argv[2], file=sys.stderr)",
            first_host_token,
            second_host_token,
        ),
        runtime_environment_factory=runtime_environment,
    )
    events: list[StepEvent] = []
    runtime_result = SubprocessExecutor().execute(
        runtime_step, observer=events.append, observe_output=True
    )
    assert first_host_token not in repr(runtime_result)
    assert second_host_token not in repr(runtime_result)
    assert all(
        first_host_token not in repr(event) and second_host_token not in repr(event)
        for event in events
    )

    limited_step = PreparedStep(
        step_id="gitlab.runtime-limit",
        argv=runtime_step.argv,
        max_output_bytes=1,
        runtime_environment_factory=runtime_environment,
    )
    with pytest.raises(ProcessOutputLimitError) as runtime_error:
        SubprocessExecutor().execute(limited_step)
    assert first_host_token not in str(runtime_error.value)
    assert second_host_token not in str(runtime_error.value)

    class FailingClient:
        def get(self, _url: str, **_kwargs: object) -> object:
            raise RuntimeError(f"authorization={secret}")

    client = GitLabHttpClient()
    client._client = FailingClient()  # type: ignore[assignment]
    with pytest.raises(GitLabIdentityError) as error:
        client.get_user("gitlab.example", secret)
    assert secret not in repr(error.value)
    assert secret not in str(error.value)
