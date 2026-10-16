"""Run the combined MYL-491 local E2E workflow against caller-owned inputs.

The test repository supplies the concrete project, addon roots, Odoo checkout,
and Python selector. This script deliberately keeps logs and diagnostics in
the caller's evidence directory; it never commits runtime state or secrets.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
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


def _run_expected_failure(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, cwd=cwd, check=False, capture_output=True, text=True)
    if result.returncode == 0:
        raise AssertionError(f"expected command to fail: {' '.join(args)}")
    return result


def _assert_dependency_projection(  # noqa: C901
    project: Path, payload: dict[str, object]
) -> None:
    result = payload.get("data", payload.get("result"))
    if not isinstance(result, dict):
        raise TypeError("prepare-venv result is missing")
    inputs = result.get("dependency_inputs")
    if not isinstance(inputs, list):
        steps = result.get("steps")
        if isinstance(steps, list):
            for step in steps:
                if not isinstance(step, dict):
                    continue
                details = step.get("details")
                if isinstance(details, dict) and isinstance(details.get("dependency_inputs"), list):
                    inputs = details["dependency_inputs"]
                    break
    if not isinstance(inputs, list):
        raise TypeError("prepare-venv did not report dependency inputs")
    paths = [Path(item) for item in inputs if isinstance(item, str)]
    if paths != list(dict.fromkeys(paths)):
        raise AssertionError("dependency inputs are not canonical and deduplicated")
    if any(not path.is_file() for path in paths):
        raise AssertionError("prepare-venv reported a missing dependency input")
    if any(str(project) not in str(path) for path in paths):
        raise AssertionError("dependency input escaped the project checkout")


def main() -> int:  # noqa: C901
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--odoo-bin", type=Path, required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--venv", type=Path, required=True)
    parser.add_argument("--addons-root", type=Path, action="append", default=[])
    parser.add_argument("--server-wide-module", action="append", default=[])
    parser.add_argument("--config", type=Path)
    parser.add_argument("--requirements", type=Path, action="append", default=[])
    parser.add_argument("--local-config", action="store_true")
    parser.add_argument(
        "--refresh-pin",
        help="Append this requirement line before the refresh assertion (caller-owned file).",
    )
    parser.add_argument("--evidence-dir", type=Path, default=Path(".myl491-evidence"))
    parser.add_argument(
        "--execute", action="store_true", help="Run mutations after dry-run checks."
    )
    args = parser.parse_args()
    project = args.project.resolve()
    evidence = args.evidence_dir.resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    manifest = project / ".odcli" / "project.toml"
    manifest_before_dry_run = manifest.read_bytes() if manifest.is_file() else None
    common = ["odcli"]
    source_config = (project / args.config).resolve() if args.config is not None else None
    init_base = [
        *common,
        "init",
        "--project",
        str(project),
        "--format",
        "json",
        "--allow-partial",
        "--odoo-bin",
        str(args.odoo_bin),
        "--python",
        args.python,
    ]
    if source_config is not None:
        init_base.extend(("--config", str(source_config)))
    if args.local_config:
        init_base.append("--local-config")
    for root in args.addons_root:
        init_base.extend(("--addons-root", str(root)))
    for module in args.server_wide_module:
        init_base.extend(("--server-wide-module", module))
    for requirement in args.requirements:
        init_base.extend(("--requirements", str(requirement)))
    dry_init = _run([*init_base, "--dry-run"], cwd=project)
    prepare_base = [
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
    ]
    dry_prepare = _run([*prepare_base, "--dry-run"], cwd=project) if manifest.is_file() else None
    (evidence / "dry-run-init.json").write_text(
        json.dumps(dry_init, indent=2) + "\n", encoding="utf-8"
    )
    if manifest_before_dry_run is None:
        if manifest.exists():
            raise AssertionError("dry-run init created a manifest")
    elif manifest.read_bytes() != manifest_before_dry_run:
        raise AssertionError("dry-run init changed the manifest")
    if not args.execute:
        if dry_prepare is None:
            raise RuntimeError(
                "prepare-venv dry-run requires an initialized project; pass --execute"
            )
        (evidence / "dry-run-prepare-venv.json").write_text(
            json.dumps(dry_prepare, indent=2) + "\n", encoding="utf-8"
        )
        _assert_dependency_projection(project, dry_prepare)
        return 0
    source_bytes = source_config.read_bytes() if source_config is not None else None
    _run([*init_base, "--yes"], cwd=project)
    manifest_after_init = manifest.read_bytes()
    if dry_prepare is None:
        dry_prepare = _run([*prepare_base, "--dry-run"], cwd=project)
    (evidence / "dry-run-prepare-venv.json").write_text(
        json.dumps(dry_prepare, indent=2) + "\n", encoding="utf-8"
    )
    _assert_dependency_projection(project, dry_prepare)
    execute_prepare = _run(prepare_base, cwd=project)
    _assert_dependency_projection(project, execute_prepare)
    config = ProjectConfig.load(project)
    interpreter = Path(str(config.python))
    if interpreter.parent.parent != args.venv.resolve():
        raise AssertionError("manifest interpreter is not inside the selected venv")
    if config.server_wide_modules != tuple(args.server_wide_module):
        raise AssertionError("server-wide module order was not preserved")
    if source_config is not None:
        if config.source_config is None:
            raise AssertionError("source config was not retained in the manifest")
        if source_config.read_bytes() != source_bytes:
            raise AssertionError("source config changed during init")
    for root in args.addons_root:
        for manifest_file in sorted((project / root).rglob("__manifest__.py")):
            if '"version"' not in manifest_file.read_text(encoding="utf-8"):
                raise AssertionError(
                    f"addon fixture has no import/version metadata: {manifest_file}"
                )

    # A repeated init is the no-op path; a changed generated file is the repair path.
    _run([*init_base, "--yes"], cwd=project)
    if manifest.read_bytes() != manifest_after_init:
        raise AssertionError("idempotent init changed the manifest")
    generated = project / ".odcli" / "odoo.conf"
    if generated.is_file():
        original_generated = generated.read_bytes()
        generated.write_bytes(original_generated + b"\n# intentional fixture drift\n")
        _run([*init_base, "--yes"], cwd=project)
        if b"intentional fixture drift" in generated.read_bytes():
            raise AssertionError("generated config repair did not remove drift")
        if manifest.read_bytes() != manifest_after_init:
            raise AssertionError("generated config repair changed the manifest")

    if args.refresh_pin is not None and args.requirements:
        requirement = args.requirements[0]
        requirement = (
            (project / requirement).resolve() if not requirement.is_absolute() else requirement
        )
        original_requirement = requirement.read_bytes()
        requirement.write_bytes(original_requirement + f"\n{args.refresh_pin}\n".encode())
        try:
            refreshed = _run(
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
            _assert_dependency_projection(project, refreshed)
        finally:
            requirement.write_bytes(original_requirement)

    # A pre-existing non-venv interpreter must fail before the manifest can persist.
    failed_venv = project / ".myl491-invalid-venv"
    invalid_interpreter = failed_venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    invalid_interpreter.parent.mkdir(parents=True, exist_ok=True)
    invalid_interpreter.write_text("not a Python interpreter\n", encoding="utf-8")
    before_failure = manifest.read_bytes()
    try:
        _run_expected_failure(
            [
                *common,
                "prepare-venv",
                "--project",
                str(project),
                "--python",
                args.python,
                "--venv",
                str(failed_venv),
                "--format",
                "json",
            ],
            cwd=project,
        )
        if manifest.read_bytes() != before_failure:
            raise AssertionError("failed prepare-venv changed the manifest")
    finally:
        shutil.rmtree(failed_venv, ignore_errors=True)
    if source_config is not None and source_config.read_bytes() != source_bytes:
        raise AssertionError("source config was modified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
