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
import urllib.error
import urllib.request
from pathlib import Path
from typing import Final

_READINESS_PATHS: Final[tuple[str, ...]] = ("/healthz", "/openapi.json")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-sha", required=True, help="installed baseline revision")
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


def _run(argv: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(argv, env=env, capture_output=True, text=True, check=False)
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
    tool_dir = root / "tool"
    user_root = root / "user"
    monitor: subprocess.Popen[str] | None = None
    try:
        tool_dir.mkdir(parents=True, exist_ok=True)
        user_root.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["ODCLI_USER_ROOT"] = str(user_root)
        baseline_requirement = (
            f"odoo-instance-sdk[dashboard] @ git+{source_repo}@{args.baseline_sha}"
        )
        _run(
            [
                args.uv,
                "tool",
                "install",
                "--force",
                "--tool-dir",
                str(tool_dir),
                baseline_requirement,
            ],
            env=env,
        )
        odcli = _require_executable(tool_dir / "bin" / "odcli")
        _run(
            [str(odcli), "update", "--ref", args.candidate_sha, "--yes", "--format", "json"],
            env=env,
        )
        port = _port(args.port)
        monitor = subprocess.Popen(
            [str(odcli), "monitor", "--headless", "--port", str(port)],
            env=env,
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
                    "candidate_sha": args.candidate_sha,
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
