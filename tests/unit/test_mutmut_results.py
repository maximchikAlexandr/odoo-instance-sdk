from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING

from scripts import mutmut_results

if TYPE_CHECKING:
    import pytest


def test_project_results_uses_exact_module_filter() -> None:
    output = (
        "odoo_instance_sdk.internal.sanitize.clean: killed\n"
        "odoo_instance_sdk.internal.sanitize.cleaner: survived\n"
        "odoo_instance_sdk.internal.urls.parse: timeout\n"
    )

    assert (
        mutmut_results._project_results(output, "odoo_instance_sdk.internal.sanitize.clean")
        == "odoo_instance_sdk.internal.sanitize.clean: killed\n"
    )


def test_main_propagates_upstream_nonzero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    completed = subprocess.CompletedProcess(
        args=["mutmut", "results"], returncode=7, stdout="upstream failure\n"
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: completed)
    monkeypatch.setattr(sys, "argv", ["mutmut_results.py", "odoo_instance_sdk.internal.*"])

    assert mutmut_results.main() == 7
    assert capsys.readouterr().out == "upstream failure\n"


def test_main_returns_empty_success_for_no_match(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    completed = subprocess.CompletedProcess(
        args=["mutmut", "results"],
        returncode=0,
        stdout="odoo_instance_sdk.internal.urls.parse: killed\n",
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: completed)
    monkeypatch.setattr(sys, "argv", ["mutmut_results.py", "odoo_instance_sdk.internal.sanitize.*"])

    assert mutmut_results.main() == 0
    assert capsys.readouterr().out == ""
