from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.integration.real_odoo.contracts import CANONICAL_LEAF_COUNT
from tests.unit.test_cli_output_modes import PUBLIC_LEAF_CASES

ROOT = Path(__file__).parents[2]


@pytest.mark.unit
@pytest.mark.parametrize(
    "script",
    [
        ROOT / "scripts" / "e2e_gitlab_member_workflow.py",
        ROOT / "scripts" / "e2e_update_dashboard.py",
    ],
)
def test_private_e2e_scripts_have_bounded_help(script: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "cleanup" in result.stdout.lower()


@pytest.mark.unit
def test_gitlab_workflow_assets_are_parameterized_and_sanitized() -> None:
    script = (ROOT / "scripts" / "e2e_gitlab_member_workflow.py").read_text(encoding="utf-8")
    assert "TEST_REPOSITORY_URL" in script
    assert "TEST_GITLAB_MEMBER_A" in script
    assert "TEST_GITLAB_MEMBER_B" in script
    assert "PRIVATE-TOKEN" not in script
    assert "shutil.rmtree(root" in script
    assert '"git",\n        "mr",\n        "create"' in script
    assert "--recurse-submodules" in script


@pytest.mark.unit
def test_update_workflow_retains_exact_revision_and_dashboard_probe() -> None:
    script = (ROOT / "scripts" / "e2e_update_dashboard.py").read_text(encoding="utf-8")
    assert "--baseline-sha" in script
    assert "--candidate-sha" in script
    assert "odoo-instance-sdk[dashboard]" in script
    assert "--ref" in script
    assert '"/healthz"' in script
    assert '"/openapi.json"' in script
    assert "multica" in script and "daemon" in script


@pytest.mark.unit
def test_public_matrix_includes_new_gitlab_leaves() -> None:
    paths = {" ".join(case.path) for case in PUBLIC_LEAF_CASES}
    assert {"git clone", "git mr create"} <= paths
    assert len(PUBLIC_LEAF_CASES) == CANONICAL_LEAF_COUNT

    matrix = (ROOT / "tests/integration/real_odoo/command-matrix.md").read_text(encoding="utf-8")
    assert "| `git clone` |" in matrix
    assert "| `git mr create` |" in matrix


@pytest.mark.unit
def test_readme_states_generic_git_boundaries_and_credential_schema() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for required in (
        "gitlab-credentials.toml",
        "ODCLI_GITLAB_TOKEN_*",
        "no GitHub support",
        "no implicit",
        "no standalone `push` command",
        "no hidden superproject",
        "`odcli git clone`",
        "`odcli git mr create`",
    ):
        assert required in readme
