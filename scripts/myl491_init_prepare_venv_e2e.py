"""Run the combined MYL-491 local E2E workflow against caller-owned inputs.

The test repository supplies the concrete project, addon roots, Odoo checkout,
and Python selector. This script deliberately keeps logs and diagnostics in
the caller's evidence directory; it never commits runtime state or secrets.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from odoo_instance_sdk.project import ProjectConfig


def _run(args: list[str], *, cwd: Path) -> dict[str, object]:
    result = subprocess.run(args, cwd=cwd, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(args)}\n{result.stderr[-4000:]}"
        )
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"command did not return JSON: {' '.join(args)}") from exc
    if not isinstance(value, dict):
        raise TypeError(f"command returned a non-object document: {' '.join(args)}")
    if value.get("ok") is not True:
        raise RuntimeError(f"command returned failure: {value}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--venv", type=Path, required=True)
    parser.add_argument("--addons-root", type=Path, action="append", default=[])
    parser.add_argument("--server-wide-module", action="append", default=[])
    parser.add_argument("--evidence-dir", type=Path, default=Path(".myl491-evidence"))
    parser.add_argument(
        "--execute", action="store_true", help="Run mutations after dry-run checks."
    )
    args = parser.parse_args()
    project = args.project.resolve()
    evidence = args.evidence_dir.resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    common = ["odcli"]
    init = [
        *common,
        "init",
        "--project",
        str(project),
        "--format",
        "json",
        "--dry-run",
        "--allow-partial",
    ]
    for root in args.addons_root:
        init.extend(("--addons-root", str(root)))
    for module in args.server_wide_module:
        init.extend(("--server-wide-module", module))
    dry_init = _run(init, cwd=project)
    dry_prepare = _run(
        [
            *common,
            "prepare-venv",
            "--project",
            str(project),
            "--python",
            args.python,
            "--venv",
            str(args.venv),
            "--format",
            "json",
            "--dry-run",
        ],
        cwd=project,
    )
    (evidence / "dry-run-init.json").write_text(
        json.dumps(dry_init, indent=2) + "\n", encoding="utf-8"
    )
    (evidence / "dry-run-prepare-venv.json").write_text(
        json.dumps(dry_prepare, indent=2) + "\n", encoding="utf-8"
    )
    if not args.execute:
        return 0
    execute_init = [item for item in init if item not in {"--format", "json", "--dry-run"}]
    init_result = subprocess.run([*execute_init, "--yes"], cwd=project, check=False)
    if init_result.returncode != 0:
        raise RuntimeError(f"execute-mode init failed ({init_result.returncode})")
    _run(
        [
            *common,
            "prepare-venv",
            "--project",
            str(project),
            "--python",
            args.python,
            "--venv",
            str(args.venv),
            "--format",
            "json",
        ],
        cwd=project,
    )
    config = ProjectConfig.load(project)
    interpreter = Path(str(config.python))
    if interpreter.parent.parent != args.venv.resolve():
        raise AssertionError("manifest interpreter is not inside the selected venv")
    if config.server_wide_modules != tuple(args.server_wide_module):
        raise AssertionError("server-wide module order was not preserved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
