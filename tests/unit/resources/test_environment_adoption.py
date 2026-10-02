from __future__ import annotations

import subprocess
from pathlib import Path

from odoo_instance_sdk.resources.environment import (
    EnvironmentCheckoutOptions,
    EnvironmentDatabaseMode,
)


def test_adopt_command_captures_external_checkout_without_git_creation(
    env_client: object, project_manifest: Path, fake_python: Path, tmp_path: Path
) -> None:
    subprocess.run(
        ["git", "-C", str(project_manifest), "remote", "add", "origin", str(project_manifest)],
        check=True,
        capture_output=True,
        text=True,
    )
    checkout = tmp_path / "adopted"
    subprocess.run(
        ["git", "clone", str(project_manifest), str(checkout)],
        check=True,
        capture_output=True,
        text=True,
    )
    manifest = project_manifest / ".odcli" / "project.toml"
    manifest.write_text(
        manifest.read_text()
        + "\n[remote_instances.staging]\n"
        + 'base_url = "https://staging.example"\n'
        + 'database = "comerta"\n'
        + 'git_branch = "main"\n'
    )
    command = env_client.environments.adopt_command(  # type: ignore[attr-defined]
        project_manifest,
        checkout,
        options=EnvironmentCheckoutOptions(
            db_mode=EnvironmentDatabaseMode.COPY,
            base_ref="main",
            remote_name="staging",
            python=str(fake_python),
        ),
    )
    plan = command._private_projection()  # type: ignore[attr-defined]
    assert plan is not None
    assert all(step.step_id != "checkout.worktree" for step in command._prepared().steps)  # type: ignore[attr-defined]
    assert checkout.is_dir()
