from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from odoo_instance_sdk.exceptions import AdminPasswordRequiredError
from odoo_instance_sdk.internal.admin_password import (
    ADMIN_PASSWORD_KEY,
    resolve_admin_password_secret,
)
from odoo_instance_sdk.internal.project_env import load_user_environment


def test_prompt_returns_secret_with_prompt_provenance() -> None:
    with (
        patch(
            "odoo_instance_sdk.internal.admin_password.getpass", side_effect=["s3cret", "s3cret"]
        ),
    ):
        secret, provenance = resolve_admin_password_secret(prompt=True, project_root=None)
    assert secret == "s3cret"
    assert provenance == "prompt"


def test_prompt_mismatch_raises() -> None:
    with (
        patch("odoo_instance_sdk.internal.admin_password.getpass", side_effect=["s3cret", "wrong"]),
        pytest.raises(AdminPasswordRequiredError, match="did not match"),
    ):
        resolve_admin_password_secret(prompt=True, project_root=None)


def test_prompt_empty_raises() -> None:
    with (
        patch("odoo_instance_sdk.internal.admin_password.getpass", side_effect=["", ""]),
        pytest.raises(AdminPasswordRequiredError, match="required"),
    ):
        resolve_admin_password_secret(prompt=True, project_root=None)


def test_environment_secret_used_when_not_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ADMIN_PASSWORD_KEY, "env-pass")
    secret, provenance = resolve_admin_password_secret(prompt=False, project_root=None)
    assert secret == "env-pass"
    assert provenance == "environment"


def test_missing_secret_raises_admin_password_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ADMIN_PASSWORD_KEY, raising=False)
    with pytest.raises(AdminPasswordRequiredError) as exc_info:
        resolve_admin_password_secret(prompt=False, project_root=None)
    assert exc_info.value.code == "admin_password_required"
    assert "s3cret" not in str(exc_info.value)


def test_dotenv_fallback_when_env_unset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(ADMIN_PASSWORD_KEY, raising=False)
    odcli_dir = tmp_path / ".odcli"
    odcli_dir.mkdir()
    env_file = odcli_dir / ".env"
    env_file.write_text(f"{ADMIN_PASSWORD_KEY}=file-pass\n")
    env_file.chmod(0o600)
    secret, provenance = resolve_admin_password_secret(prompt=False, project_root=tmp_path)
    assert secret == "file-pass"
    assert provenance == "environment"


def test_user_env_fallback_and_fixed_precedence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(ADMIN_PASSWORD_KEY, raising=False)
    user_root = tmp_path / "user"
    user_root.mkdir(mode=0o700)
    user_env = user_root / ".env"
    user_env.write_text(f"{ADMIN_PASSWORD_KEY}=user-pass\n", encoding="utf-8")
    user_env.chmod(0o600)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.admin_password.get_user_root",
        lambda **_kwargs: user_root,
    )
    assert ADMIN_PASSWORD_KEY in load_user_environment(user_root)

    assert resolve_admin_password_secret(prompt=False, project_root=None)[0] == "user-pass"

    project = tmp_path / "project"
    project_env = project / ".odcli" / ".env"
    project_env.parent.mkdir(parents=True)
    project_env.write_text(f"{ADMIN_PASSWORD_KEY}=project-pass\n", encoding="utf-8")
    project_env.chmod(0o600)
    assert resolve_admin_password_secret(prompt=False, project_root=project)[0] == "project-pass"

    monkeypatch.setenv(ADMIN_PASSWORD_KEY, "process-pass")
    assert resolve_admin_password_secret(prompt=False, project_root=project)[0] == "process-pass"


def test_secret_not_in_exception_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ADMIN_PASSWORD_KEY, "super-secret-value-123")
    monkeypatch.delenv(ADMIN_PASSWORD_KEY, raising=False)
    with pytest.raises(AdminPasswordRequiredError) as exc_info:
        resolve_admin_password_secret(prompt=False, project_root=None)
    assert "super-secret-value-123" not in str(exc_info.value)
