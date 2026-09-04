from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import InstanceConfigurationError
from odoo_instance_sdk.internal.project_runtime import resolve_project_runtime


@pytest.mark.skipif(os.name == "nt", reason="relative PATH entries differ on Windows")
def test_path_selector_is_absolute_before_runtime_cwd_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lookup = tmp_path / "lookup"
    runtime = tmp_path / "runtime"
    for root, output in ((lookup, "lookup"), (runtime, "runtime")):
        executable = root / "relbin" / "demo-python"
        executable.parent.mkdir(parents=True)
        executable.write_text(f"#!/bin/sh\nprintf '%s' '{output}'\n")
        executable.chmod(0o755)

    monkeypatch.setenv("PATH", "relbin")
    monkeypatch.chdir(lookup)
    resolved = resolve_project_runtime(lookup, "demo-python")

    assert resolved == lookup / "relbin" / "demo-python"
    assert resolved.is_absolute()

    monkeypatch.chdir(runtime)
    result = subprocess.run(
        [str(resolved)],
        cwd=runtime,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout == "lookup"


def test_non_executable_runtime_path_is_rejected(tmp_path: Path) -> None:
    runtime = tmp_path / "python"
    runtime.write_text("#!/bin/sh\nexit 0\n")

    with pytest.raises(InstanceConfigurationError, match="not executable"):
        resolve_project_runtime(tmp_path, runtime)
