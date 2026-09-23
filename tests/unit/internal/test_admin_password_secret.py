from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from odoo_instance_sdk.exceptions import AdminPasswordRequiredError
from odoo_instance_sdk.internal.admin_password import (
    ADMIN_PASSWORD_KEY,
    resolve_admin_password_secret,
)


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


def test_secret_not_in_exception_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ADMIN_PASSWORD_KEY, "super-secret-value-123")
    monkeypatch.delenv(ADMIN_PASSWORD_KEY, raising=False)
    with pytest.raises(AdminPasswordRequiredError) as exc_info:
        resolve_admin_password_secret(prompt=False, project_root=None)
    assert "super-secret-value-123" not in str(exc_info.value)
