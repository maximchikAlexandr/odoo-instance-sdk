from __future__ import annotations

import subprocess
from pathlib import Path

from odoo_instance_sdk.internal.proc import PreparedProcess, ProcessResult


def _add_named_source(project_manifest: Path) -> None:
    manifest = project_manifest / ".odcli" / "project.toml"
    manifest.write_text(
        manifest.read_text()
        + "\n[remote_instances.staging]\n"
        + 'base_url = "https://staging.example"\n'
        + 'database = "comerta"\n'
        + 'git_branch = "main"\n'
    )


def _external_checkout(project_manifest: Path, tmp_path: Path, *, linked: bool) -> Path:
    subprocess.run(
        ["git", "-C", str(project_manifest), "remote", "add", "origin", str(project_manifest)],
        check=True,
        capture_output=True,
        text=True,
    )
    checkout = tmp_path / ("linked" if linked else "clone")
    if linked:
        subprocess.run(
            [
                "git",
                "-C",
                str(project_manifest),
                "worktree",
                "add",
                "-b",
                "adopted",
                str(checkout),
                "main",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    else:
        subprocess.run(
            ["git", "clone", str(project_manifest), str(checkout)],
            check=True,
            capture_output=True,
            text=True,
        )
    subprocess.run(
        ["git", "-C", str(checkout), "config", "user.email", "test@test.com"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(checkout), "config", "user.name", "Test"],
        check=True,
        capture_output=True,
        text=True,
    )
    _add_named_source(project_manifest)
    return checkout


def _process_result(step: PreparedProcess, stdout: str = "", stderr: str = "") -> ProcessResult:
    text = getattr(step, "text", False)
    return ProcessResult(
        argv=step.argv,
        returncode=0,
        stdout=stdout if text else stdout.encode(),
        stderr=stderr if text else stderr.encode(),
        duration=0.0,
        cwd=step.cwd,  # type: ignore[attr-defined]
        environment=step.environment,  # type: ignore[attr-defined]
    )
