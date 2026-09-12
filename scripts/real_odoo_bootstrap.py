#!/usr/bin/env python3
"""Validate real-Odoo CI prerequisites before pytest provisions resources."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform as host_platform
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Final, Literal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.integration.real_odoo.pins import (  # noqa: E402
    E2E_PINS,
    PrerequisiteError,
    classify_cache,
    normalize_platform,
    pin_manifest_dict,
    validate_pins,
)

Tier = Literal["smoke", "full"]
MAX_PROBE_SECONDS: Final[float] = 30.0
ODOO_REPOSITORY: Final[str] = "https://github.com/odoo/odoo.git"
ACTIONS_CACHE: Final[str] = "6849a6489940f00c2f30c0fb92c6274307ccb58a"


def source_cache_key(os_name: str, architecture: str) -> str:
    """Return the immutable bare-source cache key from the CI contract."""
    return f"odoo19-{os_name}-{architecture}-{E2E_PINS.odoo_source_commit}"


def uv_cache_key(
    os_name: str,
    architecture: str,
    odoo_requirements: bytes,
    repository_lock: bytes,
) -> str:
    """Return the content-addressed uv cache key."""
    digest = hashlib.sha256(odoo_requirements + repository_lock).hexdigest()
    return f"uv-{os_name}-{architecture}-{E2E_PINS.cpython}-{E2E_PINS.uv}-{digest}"


def _run(
    command: list[str], *, timeout: float = MAX_PROBE_SECONDS
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout,
    )


def _version_is_exact(command: list[str], expected: str) -> bool:
    try:
        result = _run(command)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and expected in (result.stdout + result.stderr).split()


def _image_manifest_is_pinned(image: str, expected_platform_digest: str) -> bool:
    """Check the index and selected platform digest without starting a container."""
    try:
        result = _run(["docker", "buildx", "imagetools", "inspect", image])
    except (OSError, subprocess.SubprocessError):
        return False
    output = result.stdout + result.stderr
    return (
        result.returncode == 0
        and image.rsplit("@", 1)[1] in output
        and expected_platform_digest in output
    )


def _source_revision_is_available() -> bool:
    try:
        result = _run(
            ["git", "ls-remote", ODOO_REPOSITORY, E2E_PINS.odoo_source_commit],
            timeout=MAX_PROBE_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and E2E_PINS.odoo_source_commit in result.stdout


def _runner_metadata() -> dict[str, str]:
    return {
        "image_os": os.environ.get("ImageOS", "unknown"),  # noqa: SIM112
        "image_version": os.environ.get("ImageVersion", "unknown"),  # noqa: SIM112
        "runner": E2E_PINS.github_runner,
    }


def prerequisite_checks(tier: Tier, resolved_platform: str) -> dict[str, bool]:
    """Probe every required input; this function never provisions a resource."""
    docker = shutil.which("docker") is not None
    checks = {
        "docker": docker,
        "docker_daemon": docker and _run(["docker", "info"]).returncode == 0,
        "docker_compose": docker and _run(["docker", "compose", "version"]).returncode == 0,
        "python": (
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
            == E2E_PINS.cpython
        ),
        "uv": _version_is_exact(["uv", "--version"], E2E_PINS.uv),
        "odoo_image": _image_manifest_is_pinned(
            E2E_PINS.odoo_image,
            E2E_PINS.odoo_linux_amd64_manifest
            if resolved_platform == "linux/amd64"
            else E2E_PINS.odoo_linux_arm64_manifest,
        ),
        "postgres_image": _image_manifest_is_pinned(
            E2E_PINS.postgres_image,
            E2E_PINS.postgres_linux_amd64_manifest
            if resolved_platform == "linux/amd64"
            else E2E_PINS.postgres_linux_arm64_manifest,
        ),
    }
    if tier == "full":
        checks["odoo_source_revision"] = _source_revision_is_available()
    return checks


def _resolve_platform() -> str:
    return normalize_platform(host_platform.system(), host_platform.machine())


def _supported_platform(value: str) -> str:
    if value not in {"linux/amd64", "linux/arm64"}:
        raise PrerequisiteError(f"unsupported platform: {value}")
    return value


def _json_write(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def bootstrap(
    tier: Tier,
    output: Path,
    *,
    artifact_root: Path | None = None,
    run_id: str | None = None,
    platform_name: str | None = None,
) -> dict[str, object]:
    """Write a redacted machine-readable prerequisite manifest.

    A manifest is written even when validation fails.  Callers must treat a
    non-zero result as a hard failure and must not turn it into a pytest skip.
    """
    if tier not in {"smoke", "full"}:
        raise ValueError(f"unsupported tier: {tier}")
    chosen_run_id = run_id or secrets.token_hex(16)
    root = artifact_root or output.parent / chosen_run_id
    source_hit = os.environ.get("ODCLI_E2E_SOURCE_CACHE_HIT") == "true"
    uv_hit = os.environ.get("ODCLI_E2E_UV_CACHE_HIT") == "true"
    cache_class = classify_cache(source_hit=source_hit, uv_hit=uv_hit)
    manifest: dict[str, object] = {
        "schema": "odcli-real-odoo-bootstrap-v1",
        "ok": False,
        "tier": tier,
        "run_id": chosen_run_id,
        "platform": None,
        "runner": _runner_metadata(),
        "pins": {},
        "workflow_actions": {
            "checkout": E2E_PINS.actions_checkout,
            "setup_uv": E2E_PINS.setup_uv,
            "cache": ACTIONS_CACHE,
            "upload_artifact": E2E_PINS.upload_artifact,
        },
        "cache": {
            "class": cache_class,
            "source_hit": source_hit,
            "uv_hit": uv_hit,
            "source_key": None,
            "uv_key": None,
        },
        "prerequisites": {},
        "missing": [],
        "artifact_root": str(root),
    }
    try:
        validate_pins()
        resolved = _supported_platform(platform_name or _resolve_platform())
        manifest["platform"] = resolved
        manifest["pins"] = pin_manifest_dict()
        architecture = resolved.rsplit("/", 1)[1]
        cache = manifest["cache"]
        if isinstance(cache, dict):
            cache["source_key"] = source_cache_key("Linux", architecture)
            cache["uv_key"] = uv_cache_key(
                "Linux", architecture, b"", (ROOT / "uv.lock").read_bytes()
            )
        checks = prerequisite_checks(tier, resolved)
        if os.environ.get("CI", "").lower() == "true":
            checks["ci_platform_linux_amd64"] = resolved == "linux/amd64"
        manifest["prerequisites"] = checks
        missing = sorted(name for name, available in checks.items() if not available)
        manifest["missing"] = missing
        manifest["ok"] = not missing
    except (OSError, PrerequisiteError, subprocess.SubprocessError) as error:
        manifest["error"] = str(error)
        if not manifest["missing"]:
            manifest["missing"] = ["bootstrap"]
    _json_write(output, manifest)
    if bool(manifest["ok"]):
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        _json_write(root / "bootstrap-manifest.json", manifest)
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with Path(github_output).open("a", encoding="utf-8") as stream:
            stream.write(f"run_id={chosen_run_id}\n")
            stream.write(f"artifact_root={root}\n")
            stream.write(f"cache_class={cache_class}\n")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", choices=("smoke", "full"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    manifest = bootstrap(
        args.tier,
        args.output,
        artifact_root=args.artifact_root,
        run_id=args.run_id,
    )
    print(json.dumps(manifest, sort_keys=True))
    return 0 if bool(manifest["ok"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
