#!/usr/bin/env python3
"""Sanitize, bound, scan, and package real-Odoo CI evidence."""

from __future__ import annotations

import argparse
import json
import tarfile
import tempfile
from pathlib import Path
from typing import Final, Literal

from odoo_instance_sdk.internal.sanitize import sanitize_last_error, sanitize_terminal_text

Status = Literal["success", "failure"]
TEXT_LIMIT_BYTES: Final[int] = 2 * 1024 * 1024
SUCCESS_LIMIT_BYTES: Final[int] = 2 * 1024 * 1024
FAILURE_BUNDLE_LIMIT_BYTES: Final[int] = 50 * 1024 * 1024
RETENTION_DAYS: Final[int] = 7
TEXT_SUFFIXES = frozenset({".json", ".log", ".txt", ".xml", ".md", ".yml", ".yaml"})


def _bounded_text(path: Path) -> bytes:
    text = sanitize_terminal_text(
        sanitize_last_error(path.read_text(encoding="utf-8", errors="replace")) or "",
        preserve_newlines=True,
    )
    return text.encode("utf-8")[:TEXT_LIMIT_BYTES]


def _copy_bounded(source: Path, destination: Path, canary: bytes) -> None:
    if source.is_symlink() or not source.is_file():
        return
    if source.suffix.lower() in TEXT_SUFFIXES:
        content = _bounded_text(source)
    else:
        content = source.read_bytes()
    if canary and canary in content:
        raise ValueError("secret canary detected in evidence")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination.write_bytes(content)
    destination.chmod(0o600)


def _include(source: Path, status: Status) -> bool:
    if not source.is_file():
        return False
    if source.suffix.lower() not in TEXT_SUFFIXES:
        return False
    if status == "failure":
        return True
    name = source.name.lower()
    return any(
        token in name for token in ("junit", "timing", "phase", "pin", "resource", "manifest")
    )


def _write_packaging_error(output: Path, message: str) -> None:
    output.unlink(missing_ok=True)
    error_path = output.with_name("packaging-error.json")
    error_path.write_text(
        json.dumps(
            {
                "schema": "odcli-real-odoo-evidence-v1",
                "ok": False,
                "error": message,
                "retention_days": RETENTION_DAYS,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    error_path.chmod(0o600)


def _check_bundle_size(files: tuple[Path, ...], status: Status) -> None:
    if status == "success" and sum(path.stat().st_size for path in files) > SUCCESS_LIMIT_BYTES:
        raise ValueError("successful evidence exceeds 2 MiB")


def _check_archive_size(output: Path, status: Status) -> None:
    limit = SUCCESS_LIMIT_BYTES if status == "success" else FAILURE_BUNDLE_LIMIT_BYTES
    if output.stat().st_size > limit:
        raise ValueError(f"evidence bundle exceeds {limit} bytes")


def _read_canary(canary_file: Path | None) -> bytes:
    canary = canary_file.read_bytes().strip() if canary_file is not None else b""
    if canary_file is not None and len(canary) < 8:
        raise ValueError("secret canary file is missing or too short")
    return canary


def package_evidence(
    source: Path,
    output: Path,
    *,
    status: Status,
    canary_file: Path | None = None,
) -> dict[str, object]:
    """Create a bounded tar.gz and return its redacted packaging manifest."""
    if status not in {"success", "failure"}:
        raise ValueError(f"unsupported status: {status}")
    try:
        canary = _read_canary(canary_file)
        with tempfile.TemporaryDirectory(prefix="odcli-e2e-evidence-") as temporary:
            staging = Path(temporary) / "evidence"
            for path in sorted(source.rglob("*")):
                relative = path.relative_to(source)
                if relative.parts and any(part == ".git" for part in relative.parts):
                    continue
                if not _include(path, status):
                    continue
                _copy_bounded(path, staging / relative, canary)
            files = tuple(path for path in staging.rglob("*") if path.is_file())
            _check_bundle_size(files, status)
            output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            output.unlink(missing_ok=True)
            with tarfile.open(output, "w:gz") as archive:
                for path in files:
                    archive.add(path, arcname=path.relative_to(staging))
            _check_archive_size(output, status)
    except (OSError, ValueError) as error:
        _write_packaging_error(output, str(error))
        raise
    manifest = {
        "schema": "odcli-real-odoo-evidence-v1",
        "ok": True,
        "status": status,
        "artifact": output.name,
        "artifact_bytes": output.stat().st_size,
        "text_limit_bytes": TEXT_LIMIT_BYTES,
        "bundle_limit_bytes": FAILURE_BUNDLE_LIMIT_BYTES,
        "success_limit_bytes": SUCCESS_LIMIT_BYTES,
        "retention_days": RETENTION_DAYS,
        "secret_canary": "scanned-not-recorded",
    }
    manifest_path = output.with_name("evidence-manifest.json")
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
    manifest_path.chmod(0o600)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--status", choices=("success", "failure"), required=True)
    parser.add_argument("--canary-file", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            package_evidence(
                args.source,
                args.output,
                status=args.status,
                canary_file=args.canary_file,
            ),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
