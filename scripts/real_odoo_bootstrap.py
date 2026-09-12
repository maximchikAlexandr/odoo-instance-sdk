#!/usr/bin/env python3
"""Validate real-Odoo CI prerequisites before pytest provisions resources."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform as host_platform
import re
import secrets
import shutil
import subprocess
import sys
import time
from datetime import date
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
PYTHON_RESOLUTION_LOCK: Final[Path] = (
    ROOT / "tests/fixtures/real_odoo/odoo19-linux-amd64-py3.12.lock"
)
PYTHON_RESOLUTION_AUDIT: Final[Path] = (
    ROOT / "tests/fixtures/real_odoo/odoo19-linux-amd64-py3.12.audit.json"
)
AUDITED_EXCEPTION_PACKAGES: Final[frozenset[str]] = frozenset(
    {"cryptography", "pypdf2", "requests", "urllib3"}
)
PINNED_AUDIT_SCANNER: Final[str] = "2.10.1"


def source_cache_key(os_name: str, architecture: str) -> str:
    """Return the immutable bare-source cache key from the CI contract."""
    return f"odoo19-{os_name}-{architecture}-{E2E_PINS.odoo_source_commit}"


def uv_cache_key(
    os_name: str,
    architecture: str,
    odoo_requirements: bytes,
    repository_lock: bytes,
    resolution_lock: bytes = b"",
) -> str:
    """Return the content-addressed uv cache key."""
    digest = hashlib.sha256(odoo_requirements + repository_lock + resolution_lock).hexdigest()
    return f"uv-{os_name}-{architecture}-{E2E_PINS.cpython}-{E2E_PINS.uv}-{digest}"


def python_resolution_lock_is_valid(path: Path | None = None) -> bool:
    path = path or PYTHON_RESOLUTION_LOCK
    try:
        content = path.read_bytes()
    except OSError:
        return False
    return (
        hashlib.sha256(content).hexdigest() == E2E_PINS.odoo_python_lock_sha256
        and b"--hash=sha256:" in content
    )


def _locked_package_versions(content: bytes) -> dict[str, str]:
    versions: dict[str, str] = {}
    for line in content.decode("utf-8", errors="replace").splitlines():
        match = re.match(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)", line)
        if match is not None:
            versions[match.group(1).replace("_", "-").lower()] = match.group(2)
    return versions


def _run_pinned_python_audit(  # noqa: C901
    lock_path: Path,
) -> set[tuple[str, str, str]] | None:
    """Return pip-audit's canonical findings, or ``None`` on any probe error."""
    try:
        result = _run(
            [
                "uvx",
                "--from",
                f"pip-audit=={PINNED_AUDIT_SCANNER}",
                "pip-audit",
                "-r",
                str(lock_path),
                "--format",
                "json",
                "--no-deps",
            ],
            timeout=120.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode not in (0, 1):
        return None
    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, list):
        return None
    findings: set[tuple[str, str, str]] = set()
    for package_result in payload:
        if not isinstance(package_result, dict):
            return None
        package = package_result.get("name")
        version = package_result.get("version")
        vulnerabilities = package_result.get("vulns")
        if not isinstance(package, str) or not isinstance(version, str):
            return None
        if not isinstance(vulnerabilities, list):
            return None
        for vulnerability in vulnerabilities:
            if not isinstance(vulnerability, dict) or not isinstance(vulnerability.get("id"), str):
                return None
            advisory = str(vulnerability["id"])
            if not advisory:
                return None
            finding = (str(package).replace("_", "-").lower(), str(version), advisory)
            if finding in findings:
                return None
            findings.add(finding)
    return findings


def python_resolution_audit_is_valid(  # noqa: C901
    path: Path | None = None,
    *,
    lock_path: Path | None = None,
    scanner_result: set[tuple[str, str, str]] | None = None,
) -> bool:
    audit_path = path or PYTHON_RESOLUTION_AUDIT
    resolved_lock = lock_path or PYTHON_RESOLUTION_LOCK
    try:
        audit_content = audit_path.read_bytes()
        lock_content = resolved_lock.read_bytes()
        value = json.loads(audit_content)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if hashlib.sha256(audit_content).hexdigest() != E2E_PINS.odoo_python_audit_sha256:
        return False
    if not isinstance(value, dict):
        return False
    if value.get("schema") != "odoo19-python-resolution-audit-v1":
        return False
    if value.get("source_commit") != E2E_PINS.odoo_source_commit:
        return False
    if value.get("python") != E2E_PINS.cpython:
        return False
    if value.get("platform") != "x86_64-manylinux_2_17":
        return False
    if value.get("lock_sha256") != hashlib.sha256(lock_content).hexdigest():
        return False
    versions = _locked_package_versions(lock_content)
    overrides = value.get("reviewed_overrides")
    exceptions = value.get("exceptions")
    if not isinstance(overrides, dict) or not isinstance(exceptions, list):
        return False
    if set(overrides) - set(versions):
        return False
    for package, version in overrides.items():
        if not isinstance(package, str) or not isinstance(version, str):
            return False
        if versions.get(package.replace("_", "-").lower()) != version:
            return False
    expected_findings: set[tuple[str, str, str]] = set()
    observed: set[str] = set()
    for exception in exceptions:
        if not isinstance(exception, dict):
            return False
        package = exception.get("package")
        version = exception.get("version")
        advisories = exception.get("advisories")
        expires = exception.get("expires")
        if (
            not isinstance(package, str)
            or package not in AUDITED_EXCEPTION_PACKAGES
            or package in observed
            or versions.get(package) != version
            or not isinstance(advisories, list)
            or not advisories
            or not all(isinstance(item, str) and item for item in advisories)
            or not all(
                isinstance(exception.get(key), str) and exception[key]
                for key in ("rationale", "scope", "owner", "expires")
            )
            or not isinstance(expires, str)
        ):
            return False
        try:
            if date.fromisoformat(expires) < date.today():
                return False
        except ValueError:
            return False
        observed.add(package)
        for advisory in advisories:
            finding = (package.replace("_", "-").lower(), str(version), str(advisory))
            if finding in expected_findings:
                return False
            expected_findings.add(finding)
    if observed != AUDITED_EXCEPTION_PACKAGES:
        return False
    actual_findings = (
        scanner_result if scanner_result is not None else _run_pinned_python_audit(resolved_lock)
    )
    return actual_findings is not None and actual_findings == expected_findings


def _configured_cache_key(name: str, computed: str) -> str:
    configured = os.environ.get(name)
    if configured is not None and configured != computed:
        raise PrerequisiteError(f"{name} does not match computed cache key")
    return configured or computed


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


def _source_cache_path() -> Path:
    configured = os.environ.get(
        "ODCLI_E2E_ODOO_SOURCE_CACHE",
        os.environ.get("ODCLI_E2E_SOURCE_CACHE", ".cache/odoo-source"),
    )
    path = Path(configured)
    return path if path.is_absolute() else ROOT / path


def _write_cached_requirements(cache: Path) -> bool:
    requirements = _run(
        [
            "git",
            "-C",
            str(cache),
            "show",
            f"{E2E_PINS.odoo_source_commit}:requirements.txt",
        ]
    )
    if requirements.returncode:
        return False
    requirements_path = ROOT / ".cache" / "odoo-requirements.txt"
    requirements_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    requirements_path.write_text(requirements.stdout, encoding="utf-8")
    requirements_path.chmod(0o600)
    return True


def _source_revision_is_available() -> bool:
    """Populate and verify the pinned commit in the executable bare cache."""
    cache = _source_cache_path()
    try:
        if not cache.exists():
            cache.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            result = _run(["git", "init", "--bare", str(cache)])
            if result.returncode:
                return False
        check = _run(
            [
                "git",
                "-C",
                str(cache),
                "cat-file",
                "-e",
                f"{E2E_PINS.odoo_source_commit}^{{commit}}",
            ]
        )
        if check.returncode == 0:
            return _write_cached_requirements(cache)
        result = _run(
            [
                "git",
                "-C",
                str(cache),
                "fetch",
                "--depth=1",
                ODOO_REPOSITORY,
                E2E_PINS.odoo_source_commit,
            ],
            timeout=600.0,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode:
        return False
    check = _run(
        [
            "git",
            "-C",
            str(cache),
            "cat-file",
            "-e",
            f"{E2E_PINS.odoo_source_commit}^{{commit}}",
        ]
    )
    if check.returncode:
        return False
    return _write_cached_requirements(cache)


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
        checks["python_resolution_lock"] = python_resolution_lock_is_valid()
        checks["python_resolution_audit"] = python_resolution_audit_is_valid()
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


def _cache_os_arch() -> tuple[str, str]:
    os_name = os.environ.get("RUNNER_OS", "Linux")
    architecture = os.environ.get("RUNNER_ARCH")
    if not architecture:
        architecture = "ARM64" if _resolve_platform() == "linux/arm64" else "X64"
    return os_name, architecture


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
    started = time.monotonic()
    source_hit = os.environ.get("ODCLI_E2E_SOURCE_CACHE_HIT") == "true"
    uv_hit = os.environ.get("ODCLI_E2E_UV_CACHE_HIT") == "true"
    cache_class = (
        "warm"
        if uv_hit and (tier == "smoke" or source_hit)
        else classify_cache(source_hit=source_hit, uv_hit=uv_hit)
    )
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
        "bootstrap_seconds": None,
    }
    try:
        validate_pins()
        resolved = _supported_platform(platform_name or _resolve_platform())
        manifest["platform"] = resolved
        manifest["pins"] = pin_manifest_dict()
        cache_os, cache_arch = _cache_os_arch()
        manifest["architecture"] = cache_arch
        cache = manifest["cache"]
        if isinstance(cache, dict):
            cache["source_key"] = _configured_cache_key(
                "ODCLI_E2E_SOURCE_CACHE_KEY", source_cache_key(cache_os, cache_arch)
            )
        checks = prerequisite_checks(tier, resolved)
        manifest["prerequisites"] = checks
        if isinstance(cache, dict):
            requirements_path = os.environ.get(
                "ODCLI_E2E_ODOO_REQUIREMENTS", str(ROOT / ".cache" / "odoo-requirements.txt")
            )
            requirements = Path(requirements_path)
            cache["uv_key"] = _configured_cache_key(
                "ODCLI_E2E_UV_CACHE_KEY",
                uv_cache_key(
                    cache_os,
                    cache_arch,
                    requirements.read_bytes() if requirements.is_file() else b"",
                    (ROOT / "uv.lock").read_bytes(),
                    PYTHON_RESOLUTION_LOCK.read_bytes()
                    if PYTHON_RESOLUTION_LOCK.is_file()
                    else b"",
                ),
            )
        if os.environ.get("CI", "").lower() == "true":
            checks["ci_platform_linux_amd64"] = resolved == "linux/amd64"
        missing = sorted(name for name, available in checks.items() if not available)
        manifest["missing"] = missing
        manifest["ok"] = not missing
    except (OSError, PrerequisiteError, subprocess.SubprocessError) as error:
        manifest["error"] = str(error)
        if not manifest["missing"]:
            manifest["missing"] = ["bootstrap"]
    manifest["bootstrap_seconds"] = round(time.monotonic() - started, 6)
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
