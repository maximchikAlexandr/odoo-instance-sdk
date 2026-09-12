from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

from odoo_instance_sdk.internal import executables
from odoo_instance_sdk.internal.doctor import CheckResult, DoctorRemediation


def test_optional_executable_resolution_captures_absolute_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tool = tmp_path / "msgfmt"
    tool.write_text("#!/bin/sh\n")
    monkeypatch.setattr(shutil, "which", lambda _name: str(tool))

    capability = executables.resolve_optional_executable("msgfmt")

    assert capability.name == "msgfmt"
    assert capability.path == str(tool.resolve())
    assert capability.available is True


def test_optional_executable_absence_is_nonfatal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    capability = executables.resolve_optional_executable("git-absorb")

    assert capability.path is None
    assert capability.available is False


def test_doctor_remediation_is_secret_free_and_serializable() -> None:
    remediation = DoctorRemediation(
        description="Synchronize dependencies",
        argv=("odcli", "env", "sync", "--dry-run"),
        mutating=True,
        dry_run_supported=True,
    )
    check = CheckResult(
        "dependencies", "warn", "requirements.lock missing", remediations=(remediation,)
    )

    assert remediation.supports_dry_run is True
    assert check.remediations[0].as_dict() == {
        "description": "Synchronize dependencies",
        "argv": ["odcli", "env", "sync", "--dry-run"],
        "mutating": True,
        "dry_run_supported": True,
    }
