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
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.real_odoo_pins import (  # noqa: E402
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
ACTIONS_CACHE: Final[str] = "0400d5f644dc74513175e3cd8d07132dd4860809"
PYTHON_RESOLUTION_LOCK: Final[Path] = (
    ROOT / "tests/fixtures/real_odoo/odoo19-linux-amd64-py3.12.lock"
)
PYTHON_RESOLUTION_AUDIT: Final[Path] = (
    ROOT / "tests/fixtures/real_odoo/odoo19-linux-amd64-py3.12.audit.json"
)
AUDITED_EXCEPTION_PACKAGES: Final[frozenset[str]] = frozenset(
    {"cryptography", "pypdf2", "requests", "urllib3"}
)
PINNED_AUDIT_SCANNER: Final[str] = E2E_PINS.pip_audit
PACKAGE_NAME = re.compile(r"\A[A-Za-z0-9]+(?:[-_.][A-Za-z0-9]+)*\Z")
VERSION = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9.!+_-]*\Z")
ADVISORY = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.:-]*\Z")


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
            package = _canonical_package(match.group(1))
            if package is not None:
                versions[package] = match.group(2)
    return versions


def _canonical_package(value: object) -> str | None:
    if (
        not isinstance(value, str)
        or len(value.encode("utf-8")) > 128
        or not PACKAGE_NAME.fullmatch(value)
    ):
        return None
    return re.sub(r"[-_.]+", "-", value).lower()


def _valid_version(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value.encode("utf-8")) <= 128
        and VERSION.fullmatch(value) is not None
    )


def _valid_advisory(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value.encode("utf-8")) <= 256
        and ADVISORY.fullmatch(value) is not None
    )


def _valid_exception_text(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value.encode("utf-8")) <= 4096
        and all(character not in value for character in ("\x00", "\r"))
    )


def _audit_scanner_label() -> str:
    package, separator, version = PINNED_AUDIT_SCANNER.partition("==")
    return f"{package} {version}" if separator else PINNED_AUDIT_SCANNER


def _validate_scanner_fix(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    if set(value) == {"name", "version", "skip_reason"}:
        return (
            _canonical_package(value.get("name")) is not None
            and _valid_version(value.get("version"))
            and _valid_exception_text(value.get("skip_reason"))
        )
    if set(value) == {"name", "old_version", "new_version"}:
        return (
            _canonical_package(value.get("name")) is not None
            and _valid_version(value.get("old_version"))
            and _valid_version(value.get("new_version"))
        )
    return False


def _validate_scanner_vulnerability(value: object) -> tuple[str, tuple[str, ...]] | None:
    if not isinstance(value, dict):
        return None
    if not set(value).issubset({"id", "fix_versions", "aliases", "description"}):
        return None
    advisory = value.get("id")
    fix_versions = value.get("fix_versions")
    if not _valid_advisory(advisory) or not isinstance(fix_versions, list):
        return None
    if not all(_valid_version(item) for item in fix_versions):
        return None
    aliases = value.get("aliases")
    if aliases is not None and (
        not isinstance(aliases, list) or not all(_valid_advisory(item) for item in aliases)
    ):
        return None
    description = value.get("description")
    if description is not None and not _valid_exception_text(description):
        return None
    if not isinstance(advisory, str) or not all(isinstance(item, str) for item in fix_versions):
        return None
    return advisory, tuple(fix_versions)


def _parse_scanner_payload(payload: object) -> set[tuple[str, str, str]] | None:  # noqa: C901
    if not isinstance(payload, dict) or set(payload) != {"dependencies", "fixes"}:
        return None
    dependencies = payload.get("dependencies")
    fixes = payload.get("fixes")
    if not isinstance(dependencies, list) or not isinstance(fixes, list):
        return None
    if not all(_validate_scanner_fix(value) for value in fixes):
        return None

    findings: set[tuple[str, str, str]] = set()
    for dependency in dependencies:
        if not isinstance(dependency, dict):
            return None
        package = _canonical_package(dependency.get("name"))
        if package is None:
            return None
        if set(dependency) == {"name", "skip_reason"}:
            return None
        if set(dependency) != {"name", "version", "vulns"}:
            return None
        version = dependency.get("version")
        vulnerabilities = dependency.get("vulns")
        if not _valid_version(version) or not isinstance(vulnerabilities, list):
            return None
        if not isinstance(version, str):
            return None
        for vulnerability in vulnerabilities:
            parsed = _validate_scanner_vulnerability(vulnerability)
            if parsed is None:
                return None
            advisory, _fix_versions = parsed
            finding = (package, version, advisory)
            findings.add(finding)
    return findings


def _record_audit_diagnostic(diagnostics: list[str] | None, value: str) -> None:
    if diagnostics is not None:
        diagnostics.append(value)


def _run_pinned_python_audit(
    lock_path: Path,
    diagnostics: list[str] | None = None,
) -> set[tuple[str, str, str]] | None:
    """Return pip-audit findings or a bounded, sanitized probe diagnostic."""
    try:
        result = _run(
            [
                "uvx",
                "--from",
                PINNED_AUDIT_SCANNER,
                "pip-audit",
                "-r",
                str(lock_path),
                "--format",
                "json",
                "--no-deps",
                "--disable-pip",
                "--require-hashes",
                "--strict",
                "--desc",
                "off",
                "--aliases",
                "off",
            ],
            timeout=120.0,
        )
    except (OSError, subprocess.SubprocessError) as error:
        failure = "scanner_timeout" if isinstance(error, subprocess.TimeoutExpired) else (
            f"scanner_execution_error:{type(error).__name__}"
        )
        _record_audit_diagnostic(diagnostics, failure)
        return None
    if result.returncode not in (0, 1):
        scanner_output = f"{result.stdout}\n{result.stderr}".lower()
        transport_errors = ("connection error", "connection refused", "tls error")
        if any(term in scanner_output for term in transport_errors):
            failure = "scanner_transport_failure"
        elif "no matching distribution" in scanner_output or "failed to resolve" in scanner_output:
            failure = "scanner_distribution_resolution_failure"
        else:
            failure = f"scanner_exit_code:{result.returncode}"
        _record_audit_diagnostic(diagnostics, failure)
        return None
    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError):
        _record_audit_diagnostic(diagnostics, "scanner_invalid_json")
        return None
    findings = _parse_scanner_payload(payload)
    if findings is None:
        _record_audit_diagnostic(diagnostics, "scanner_invalid_payload")
    return findings


def python_resolution_audit_is_valid(  # noqa: C901
    path: Path | None = None,
    *,
    lock_path: Path | None = None,
    scanner_result: set[tuple[str, str, str]] | None = None,
    diagnostics: list[str] | None = None,
) -> bool:
    def reject(reason: str) -> bool:
        _record_audit_diagnostic(diagnostics, reason)
        return False

    audit_path = path or PYTHON_RESOLUTION_AUDIT
    resolved_lock = lock_path or PYTHON_RESOLUTION_LOCK
    try:
        audit_content = audit_path.read_bytes()
        lock_content = resolved_lock.read_bytes()
    except OSError:
        return reject("audit_or_lock_unreadable")
    try:
        value = json.loads(audit_content)
    except (UnicodeError, json.JSONDecodeError):
        return reject("audit_invalid_json")
    if hashlib.sha256(audit_content).hexdigest() != E2E_PINS.odoo_python_audit_sha256:
        return reject("audit_hash_mismatch")
    if not isinstance(value, dict):
        return reject("audit_schema_invalid")
    if value.get("schema") != "odoo19-python-resolution-audit-v1":
        return reject("audit_schema_invalid")
    if value.get("scanner") != _audit_scanner_label():
        return reject("audit_scanner_pin_mismatch")
    if value.get("source_commit") != E2E_PINS.odoo_source_commit:
        return reject("audit_source_commit_pin_mismatch")
    if value.get("python") != E2E_PINS.cpython:
        return reject("audit_python_pin_mismatch")
    if value.get("platform") != "x86_64-manylinux_2_17":
        return reject("audit_platform_mismatch")
    if value.get("lock_sha256") != hashlib.sha256(lock_content).hexdigest():
        return reject("audit_lock_hash_mismatch")
    versions = _locked_package_versions(lock_content)
    overrides = value.get("reviewed_overrides")
    exceptions = value.get("exceptions")
    if not isinstance(overrides, dict) or not isinstance(exceptions, list):
        return reject("audit_allowlist_invalid")
    normalized_overrides: set[str] = set()
    for package, version in overrides.items():
        canonical_package = _canonical_package(package)
        if canonical_package is None or canonical_package in normalized_overrides:
            return reject("audit_allowlist_invalid")
        if not _valid_version(version) or versions.get(canonical_package) != version:
            return reject("audit_allowlist_invalid")
        normalized_overrides.add(canonical_package)
    expected_findings: set[tuple[str, str, str]] = set()
    observed: set[str] = set()
    for exception in exceptions:
        if not isinstance(exception, dict):
            return reject("audit_allowlist_invalid")
        package = exception.get("package")
        version = exception.get("version")
        advisories = exception.get("advisories")
        expires = exception.get("expires")
        canonical_package = _canonical_package(package)
        if not isinstance(version, str) or VERSION.fullmatch(version) is None:
            return reject("audit_allowlist_invalid")
        if (
            canonical_package is None
            or canonical_package not in AUDITED_EXCEPTION_PACKAGES
            or canonical_package in observed
            or versions.get(canonical_package) != version
            or not isinstance(advisories, list)
            or not advisories
            or not all(_valid_advisory(item) for item in advisories)
            or len(set(advisories)) != len(advisories)
            or not all(
                _valid_exception_text(exception.get(key)) for key in ("rationale", "scope", "owner")
            )
            or not _valid_exception_text(expires)
        ):
            return reject("audit_allowlist_invalid")
        if not isinstance(expires, str):
            return reject("audit_allowlist_invalid")
        try:
            if datetime.fromisoformat(expires).date() < datetime.now(UTC).date():
                return reject("audit_allowlist_expired")
        except ValueError:
            return reject("audit_allowlist_invalid")
        observed.add(canonical_package)
        for advisory in advisories:
            finding = (canonical_package, version, advisory)
            if finding in expected_findings:
                return reject("audit_allowlist_duplicate_finding")
            expected_findings.add(finding)
    if observed != AUDITED_EXCEPTION_PACKAGES:
        return reject("audit_allowlist_package_set_mismatch")
    if scanner_result is None:
        actual_findings = (
            _run_pinned_python_audit(resolved_lock)
            if diagnostics is None
            else _run_pinned_python_audit(resolved_lock, diagnostics)
        )
    else:
        actual_findings = scanner_result
    if actual_findings is None:
        if diagnostics:
            return False
        return reject("scanner_probe_failed")
    if actual_findings != expected_findings:
        unexpected = sorted(actual_findings - expected_findings)
        missing = sorted(expected_findings - actual_findings)
        _record_audit_diagnostic(
            diagnostics,
            "scanner_advisory_mismatch:"
            f"unexpected_count={len(unexpected)};unexpected_sample={unexpected[:20]};"
            f"missing_count={len(missing)};missing_sample={missing[:20]}",
        )
        return False
    return True


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


def _source_revision_is_available(diagnostics: list[str] | None = None) -> bool:
    """Populate and verify the pinned commit in the executable bare cache."""
    cache = _source_cache_path()
    try:
        if not cache.exists():
            cache.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            result = _run(["git", "init", "--bare", str(cache)])
            if result.returncode:
                _record_audit_diagnostic(
                    diagnostics, f"source_cache_init_exit_code:{result.returncode}"
                )
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
            if not _write_cached_requirements(cache):
                _record_audit_diagnostic(diagnostics, "pinned_source_requirements_missing")
                return False
            return True
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
    except (OSError, subprocess.SubprocessError) as error:
        _record_audit_diagnostic(diagnostics, f"source_probe_error:{type(error).__name__}")
        return False
    if result.returncode:
        _record_audit_diagnostic(diagnostics, f"source_fetch_exit_code:{result.returncode}")
        return False
    try:
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
    except (OSError, subprocess.SubprocessError) as error:
        _record_audit_diagnostic(diagnostics, f"source_probe_error:{type(error).__name__}")
        return False
    if check.returncode:
        _record_audit_diagnostic(diagnostics, "pinned_source_commit_missing")
        return False
    if not _write_cached_requirements(cache):
        _record_audit_diagnostic(diagnostics, "pinned_source_requirements_missing")
        return False
    return True


def _runner_metadata() -> dict[str, str]:
    return {
        "image_os": os.environ.get("ImageOS", "unknown"),  # noqa: SIM112
        "image_version": os.environ.get("ImageVersion", "unknown"),  # noqa: SIM112
        "runner": E2E_PINS.github_runner,
    }


def prerequisite_checks(
    tier: Tier,
    resolved_platform: str,
    diagnostics: dict[str, str] | None = None,
) -> dict[str, bool]:
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
        lock_valid = python_resolution_lock_is_valid()
        checks["python_resolution_lock"] = lock_valid
        audit_failures: list[str] = []
        if not lock_valid:
            audit_valid = False
        elif diagnostics is None:
            audit_valid = python_resolution_audit_is_valid()
        else:
            audit_valid = python_resolution_audit_is_valid(diagnostics=audit_failures)
        checks["python_resolution_audit"] = audit_valid
        if diagnostics is not None and not lock_valid:
            diagnostics["python_resolution_lock"] = "lock_hash_or_hash_directive_invalid"
            diagnostics["python_resolution_audit"] = "not_checked_lock_invalid"
        if diagnostics is not None and audit_failures:
            diagnostics["python_resolution_audit"] = ";".join(audit_failures)
        if audit_valid:
            source_failures: list[str] = []
            source_valid = (
                _source_revision_is_available()
                if diagnostics is None
                else _source_revision_is_available(source_failures)
            )
            checks["odoo_source_revision"] = source_valid
            if diagnostics is not None and source_failures:
                diagnostics["odoo_source_revision"] = ";".join(source_failures)
        else:
            checks["odoo_source_revision"] = False
            if diagnostics is not None:
                diagnostics["odoo_source_revision"] = "not_checked_audit_invalid"
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
        "diagnostics": {},
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
        diagnostics: dict[str, str] = {}
        checks = prerequisite_checks(tier, resolved, diagnostics)
        manifest["prerequisites"] = checks
        manifest["diagnostics"] = diagnostics
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
