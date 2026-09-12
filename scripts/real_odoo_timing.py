#!/usr/bin/env python3
"""Record monotonic real-Odoo setup, test, and cleanup phase durations."""

from __future__ import annotations

import argparse
import json
import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Final, Literal

Phase = Literal["setup", "test", "cleanup"]
PHASES: Final = frozenset({"setup", "test", "cleanup"})


def _read(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {"schema": "odcli-real-odoo-timing-v1", "phases": {}}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("phases"), dict):
        raise TypeError("invalid timing manifest")
    return value


def _write(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def start(path: Path, phase: Phase, *, now: float | None = None) -> None:
    if phase not in PHASES:
        raise ValueError(f"unsupported phase: {phase}")
    manifest = _read(path)
    phases = manifest["phases"]
    if not isinstance(phases, dict):
        raise TypeError("invalid timing phases")
    if phase in phases:
        raise ValueError(f"phase already started: {phase}")
    phases[phase] = {"started_monotonic": now if now is not None else time.monotonic()}
    _write(path, manifest)


def finish(path: Path, phase: Phase, *, now: float | None = None) -> float:
    manifest = _read(path)
    phases = manifest["phases"]
    if not isinstance(phases, dict) or not isinstance(phases.get(phase), dict):
        raise TypeError(f"phase was not started: {phase}")
    record = phases[phase]
    started = record.get("started_monotonic")
    if not isinstance(started, (int, float)):
        raise TypeError(f"phase has no monotonic start: {phase}")
    duration = round(max(0.0, (now if now is not None else time.monotonic()) - started), 6)
    record["duration_seconds"] = duration
    _write(path, manifest)
    return duration


def record(path: Path, phase: Phase, started: float, finished: float) -> float:
    """Add one measured lifecycle segment to a phase."""
    if phase not in PHASES:
        raise ValueError(f"unsupported phase: {phase}")
    manifest = _read(path)
    phases = manifest["phases"]
    if not isinstance(phases, dict):
        raise TypeError("invalid timing phases")
    record_value = phases.setdefault(
        phase,
        {
            "started_monotonic": started,
            "duration_seconds": 0.0,
            "segments": 0,
        },
    )
    if not isinstance(record_value, dict):
        raise TypeError(f"invalid phase record: {phase}")
    duration = round(max(0.0, finished - started), 6)
    previous = record_value.get("duration_seconds", 0.0)
    if not isinstance(previous, (int, float)):
        raise TypeError(f"invalid phase duration: {phase}")
    segments = record_value.get("segments", 0)
    if not isinstance(segments, int):
        raise TypeError(f"invalid phase segments: {phase}")
    record_value["duration_seconds"] = round(previous + duration, 6)
    record_value["finished_monotonic"] = finished
    record_value["segments"] = segments + 1
    _write(path, manifest)
    return duration


def pytest_sessionfinish(_session: object, _exitstatus: int) -> Iterator[None]:
    """Measure test completion before session fixture finalizers run."""
    configured = os.environ.get("ODCLI_E2E_TIMING_FILE")
    if not configured:
        yield
        return
    path = Path(configured)
    finish(path, "test")
    yield


try:
    import pytest
except ImportError:
    pass
else:
    pytest.hookimpl(hookwrapper=True, tryfirst=True)(pytest_sessionfinish)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "finish"))
    parser.add_argument("--phase", choices=tuple(sorted(PHASES)), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "start":
        start(args.output, args.phase)
    else:
        finish(args.output, args.phase)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
