#!/usr/bin/env python3
"""Verify a dashboard-enabled uv-tool update in an isolated local root.

This is a Tester-owned live harness.  It installs only the supplied revisions,
invokes the public ``odcli update`` path, probes the documented API, and stops
only the monitor process it started.  Raw logs and environment dumps are never
printed or committed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Final

_READINESS_PATHS: Final[tuple[str, ...]] = ("/healthz", "/openapi.json")
_EXTRA_PRESERVING_UPDATER_SHA: Final[str] = "3b8e1174f09f280c80107444d859a0a01acd07ef"
_INSTALL_TIMEOUT_SECONDS: Final[float] = 180.0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-sha", required=True, help="installed baseline revision")
    parser.add_argument(
        "--updater-sha",
        default=_EXTRA_PRESERVING_UPDATER_SHA,
        help="pinned updater revision used for the after evidence",
    )
    parser.add_argument("--candidate-sha", required=True, help="exact update revision")
    parser.add_argument(
        "--source-repo",
        default="https://github.com/maximchikAlexandr/odoo-instance-sdk.git",
        help="supported public Git source (private URLs are accepted only from env)",
    )
    parser.add_argument("--uv", default="uv", help="uv executable")
    parser.add_argument("--port", type=int, default=0, help="monitor port, or 0 to allocate one")
    parser.add_argument("--timeout", type=float, default=30.0, help="readiness timeout in seconds")
    parser.add_argument("--work-root", type=Path, help="isolated root; removed during cleanup")
    parser.add_argument("--keep-local", action="store_true", help="retain the root for debugging")
    return parser


def _run(
    argv: list[str], *, env: dict[str, str], timeout: float
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            argv,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"command timed out after {timeout:g} seconds") from exc
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {argv[0]}")
    return result


def _port(requested: int) -> int:
    if requested:
        return requested
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _probe(base_url: str, timeout: float) -> dict[str, int | bool]:
    result: dict[str, int | bool] = {}
    deadline = time.monotonic() + timeout
    for path in _READINESS_PATHS:
        while True:
            try:
                with urllib.request.urlopen(base_url + path, timeout=2.0) as response:
                    result[path] = response.status == 200
                    break
            except (OSError, urllib.error.URLError):
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"monitor readiness timeout: {path}") from None
                time.sleep(0.25)
    return result


def _daemon_pid() -> int | None:
    result = subprocess.run(
        ["multica", "daemon", "status", "--output", "json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    value = payload.get("pid") if isinstance(payload, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _stop_owned(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    daemon_pid = _daemon_pid()
    if daemon_pid == process.pid:
        raise RuntimeError("refusing to terminate the Multica daemon PID")
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _require_executable(path: Path) -> Path:
    if not path.is_file():
        raise RuntimeError("isolated uv tool did not provide odcli")
    return path


def _install_tool(
    uv: str,
    requirement: str,
    *,
    tool_dir: Path,
    tool_bin: Path,
    env: dict[str, str],
) -> None:
    install_env = dict(env)
    install_env.update({"UV_TOOL_DIR": str(tool_dir), "UV_TOOL_BIN_DIR": str(tool_bin)})
    _run(
        [uv, "tool", "install", "--force", requirement],
        env=install_env,
        timeout=_INSTALL_TIMEOUT_SECONDS,
    )


def _source_requirement(source_repo: str, ref: str) -> str:
    return f"odoo-instance-sdk[dashboard] @ git+{source_repo.removeprefix('git+')}@{ref}"


def _receipt_has_dashboard(tool_dir: Path) -> bool:
    receipt = tool_dir / "odoo-instance-sdk" / "uv-receipt.toml"
    try:
        payload = tomllib.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return False
    tool = payload.get("tool")
    requirements = tool.get("requirements") if isinstance(tool, dict) else None
    if not isinstance(requirements, list):
        return False
    matches = [
        item
        for item in requirements
        if isinstance(item, dict) and item.get("name") == "odoo-instance-sdk"
    ]
    if len(matches) != 1:
        return False
    extras = matches[0].get("extras")
    return isinstance(extras, list) and "dashboard" in extras


def _dashboard_runtime(tool_dir: Path, *, env: dict[str, str]) -> dict[str, bool]:
    """Inspect only fixed booleans from the installed interpreter."""
    python = tool_dir / "odoo-instance-sdk" / "bin" / "python"
    if not python.is_file():
        return {"imports": False, "assets": False}
    probe = subprocess.run(
        [
            str(python),
            "-c",
            (
                "import json\n"
                "from pathlib import Path\n"
                "try:\n"
                " import fastapi, starlette, uvicorn, odoo_instance_sdk\n"
                " root = Path(odoo_instance_sdk.__file__).resolve().parent / 'web' / 'dist'\n"
                " print(json.dumps({'imports': True, 'assets': (root / 'index.html').is_file() and "
                "(root / 'assets').is_dir() and any((root / 'assets').iterdir())}))\n"
                "except ImportError:\n"
                " print(json.dumps({'imports': False, 'assets': False}))\n"
            ),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        return {"imports": False, "assets": False}
    try:
        payload = json.loads(probe.stdout)
    except json.JSONDecodeError:
        return {"imports": False, "assets": False}
    return {
        "imports": payload.get("imports") is True,
        "assets": payload.get("assets") is True,
    }


def _require_historical_loss(
    update_result: subprocess.CompletedProcess[str],
    *,
    dashboard_extra: bool,
    imports: bool,
) -> None:
    if update_result.returncode != 0 or dashboard_extra or imports:
        raise RuntimeError("historical baseline did not reproduce dashboard dependency loss")


def _require_dashboard_after(*, dashboard_extra: bool, runtime: dict[str, bool]) -> None:
    if not dashboard_extra or not all(runtime.values()):
        raise RuntimeError("extra-preserving updater did not retain dashboard installation")


def _script_sha() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    source_repo = os.environ.get("TEST_UPDATE_SOURCE_REPO", args.source_repo)
    root = (
        args.work_root.expanduser().resolve()
        if args.work_root
        else Path(tempfile.mkdtemp(prefix="odcli-update-e2e-"))
    )
    before_root = root / "before"
    after_root = root / "after"
    monitor: subprocess.Popen[str] | None = None
    before_tool_dir = before_root / "tool"
    before_tool_bin = before_root / "bin"
    after_tool_dir = after_root / "tool"
    after_tool_bin = after_root / "bin"
    user_root = after_root / "user"
    try:
        before_tool_dir.mkdir(parents=True, exist_ok=True)
        after_tool_dir.mkdir(parents=True, exist_ok=True)
        user_root.mkdir(parents=True, exist_ok=True)
        before_env = os.environ.copy()
        before_env.update(
            {
                "ODCLI_USER_ROOT": str(before_root / "user"),
                "UV_TOOL_DIR": str(before_tool_dir),
                "UV_TOOL_BIN_DIR": str(before_tool_bin),
            }
        )
        baseline_requirement = _source_requirement(source_repo, args.baseline_sha)
        _install_tool(
            args.uv,
            baseline_requirement,
            tool_dir=before_tool_dir,
            tool_bin=before_tool_bin,
            env=before_env,
        )
        before_odcli = _require_executable(before_tool_bin / "odcli")
        before_update = subprocess.run(
            [
                str(before_odcli),
                "update",
                "--ref",
                args.candidate_sha,
                "--yes",
                "--format",
                "json",
            ],
            env=before_env,
            capture_output=True,
            text=True,
            check=False,
        )
        before_runtime = _dashboard_runtime(before_tool_dir, env=before_env)
        before_extra = _receipt_has_dashboard(before_tool_dir)
        _require_historical_loss(
            before_update,
            dashboard_extra=before_extra,
            imports=before_runtime["imports"],
        )

        after_env = os.environ.copy()
        after_env.update(
            {
                "ODCLI_USER_ROOT": str(user_root),
                "UV_TOOL_DIR": str(after_tool_dir),
                "UV_TOOL_BIN_DIR": str(after_tool_bin),
            }
        )
        updater_requirement = _source_requirement(source_repo, args.updater_sha)
        _install_tool(
            args.uv,
            updater_requirement,
            tool_dir=after_tool_dir,
            tool_bin=after_tool_bin,
            env=after_env,
        )
        odcli = _require_executable(after_tool_bin / "odcli")
        _run(
            [str(odcli), "update", "--ref", args.candidate_sha, "--yes", "--format", "json"],
            env=after_env,
            timeout=_INSTALL_TIMEOUT_SECONDS,
        )
        after_runtime = _dashboard_runtime(after_tool_dir, env=after_env)
        _require_dashboard_after(
            dashboard_extra=_receipt_has_dashboard(after_tool_dir),
            runtime=after_runtime,
        )
        port = _port(args.port)
        monitor = subprocess.Popen(
            [str(odcli), "monitor", "--headless", "--port", str(port)],
            env=after_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        probes = _probe(f"http://127.0.0.1:{port}", args.timeout)
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)[:1200]}, sort_keys=True))
        return 1
    else:
        print(
            json.dumps(
                {
                    "ok": True,
                    "historical_baseline_sha": args.baseline_sha,
                    "updater_source_sha": args.updater_sha,
                    "candidate_sha": args.candidate_sha,
                    "before": {
                        "outcome": "expected_dependency_loss",
                        "update_returncode": before_update.returncode,
                        "dashboard_extra": before_extra,
                        **before_runtime,
                    },
                    "after": {
                        "outcome": "updated_with_extra",
                        "dashboard_extra": True,
                        **after_runtime,
                    },
                    "probes": probes,
                    "script_sha": _script_sha(),
                },
                sort_keys=True,
            )
        )
        return 0
    finally:
        try:
            _stop_owned(monitor)
        finally:
            if not args.keep_local:
                shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
