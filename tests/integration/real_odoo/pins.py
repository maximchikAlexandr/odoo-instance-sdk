"""Immutable real-Odoo inputs and fail-closed platform/budget checks."""

from __future__ import annotations

import json
import platform as host_platform
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Literal

Tier = Literal["smoke", "full"]
CacheClass = Literal["cold", "warm"]
SUPPORTED_PLATFORMS = frozenset({"linux/amd64", "linux/arm64"})
SUCCESS_ARTIFACT_LIMIT_BYTES = 2 * 1024 * 1024
FAILURE_BUNDLE_LIMIT_BYTES = 50 * 1024 * 1024
_SHA1 = re.compile(r"\A[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class E2EPinManifest:
    odoo_source_commit: str
    odoo_image: str
    odoo_linux_amd64_manifest: str
    odoo_linux_arm64_manifest: str
    postgres_image: str
    postgres_linux_amd64_manifest: str
    postgres_linux_arm64_manifest: str
    cpython: str
    uv: str
    actions_checkout: str
    setup_uv: str
    upload_artifact: str
    github_runner: str


E2E_PINS = E2EPinManifest(
    odoo_source_commit="cd992ceebbaf343c03e1941d39cfe423d35ba6c6",
    odoo_image="docker.io/library/odoo@sha256:a627eda6b4154eead21c4fca55f84f1671d870ca111aa57f93ca305861bc4613",
    odoo_linux_amd64_manifest="sha256:515f8d24be9fed0b00804211c2bed4e50b1c7405a2b7e18c12e81b2292a84c88",
    odoo_linux_arm64_manifest="sha256:78da2811af8e18a453f341260406ae9c872c793081ac674681b09b7a7018d3a2",
    postgres_image="docker.io/library/postgres@sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685",
    postgres_linux_amd64_manifest="sha256:075f7ba66bc9b3ce7d6b8b635208ff61cd7cf1a67d71ec530eec5d7ae0cbe571",
    postgres_linux_arm64_manifest="sha256:738d1359df5aa0b6d50a9071e989c49fdd39152a2a805c6ff131bf5e2243e0b3",
    cpython="3.12.13",
    uv="0.10.8",
    actions_checkout="11d5960a326750d5838078e36cf38b85af677262",
    setup_uv="d0cc045d04ccac9d8b7881df0226f9e82c39688e",
    upload_artifact="ea165f8d65b6e75b540449e92b4886f43607fa02",
    github_runner="ubuntu-24.04",
)


@dataclass(frozen=True)
class PhaseBudget:
    setup_seconds: int
    test_seconds: int
    job_seconds: int
    success_artifact_bytes: int = SUCCESS_ARTIFACT_LIMIT_BYTES


PHASE_BUDGETS: Mapping[tuple[Tier, CacheClass], PhaseBudget] = MappingProxyType(
    {
        ("smoke", "cold"): PhaseBudget(360, 180, 600),
        ("smoke", "warm"): PhaseBudget(180, 180, 600),
        ("full", "cold"): PhaseBudget(900, 600, 1500),
        ("full", "warm"): PhaseBudget(420, 600, 1500),
    }
)
PLATFORM_IMAGE_MANIFESTS: Mapping[str, tuple[str, str]] = MappingProxyType(
    {
        "linux/amd64": (
            E2E_PINS.odoo_linux_amd64_manifest,
            E2E_PINS.postgres_linux_amd64_manifest,
        ),
        "linux/arm64": (
            E2E_PINS.odoo_linux_arm64_manifest,
            E2E_PINS.postgres_linux_arm64_manifest,
        ),
    }
)


class PrerequisiteError(RuntimeError):
    """Raised before provisioning when required inputs are unavailable."""


def normalize_platform(system: str, machine: str) -> str:
    normalized_system = system.strip().lower()
    normalized_machine = machine.strip().lower()
    architectures = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}
    architecture = architectures.get(normalized_machine)
    if normalized_system == "darwin" and normalized_machine == "arm64":
        normalized_system = "linux"
    if normalized_system != "linux" or architecture is None:
        raise PrerequisiteError(f"unsupported platform: {normalized_system}/{normalized_machine}")
    return f"linux/{architecture}"


def current_platform() -> str:
    return normalize_platform(host_platform.system(), host_platform.machine())


def validate_platform(platform: str) -> str:
    normalized = platform.strip().lower()
    if normalized not in SUPPORTED_PLATFORMS:
        raise PrerequisiteError(f"unsupported platform: {platform}")
    return normalized


def _validate_digest(value: str, *, label: str) -> None:
    if not value.startswith("sha256:") or not _SHA256.fullmatch(value[7:]):
        raise PrerequisiteError(f"malformed {label}: {value}")


def _validate_pin_values(pins: E2EPinManifest) -> None:
    if not _SHA1.fullmatch(pins.odoo_source_commit):
        raise PrerequisiteError("Odoo source must be pinned to a 40-character commit SHA")
    for value in (
        pins.odoo_linux_amd64_manifest,
        pins.odoo_linux_arm64_manifest,
        pins.postgres_linux_amd64_manifest,
        pins.postgres_linux_arm64_manifest,
    ):
        _validate_digest(value, label="platform digest")
    for image in (pins.odoo_image, pins.postgres_image):
        if "@sha256:" not in image:
            raise PrerequisiteError(f"mutable or malformed image pin: {image}")
        _validate_digest(image.rsplit("@", 1)[1], label="image pin")
    for action in (pins.actions_checkout, pins.setup_uv, pins.upload_artifact):
        if not _SHA1.fullmatch(action):
            raise PrerequisiteError(f"GitHub Action is not pinned to a commit SHA: {action}")
    if pins.cpython != "3.12.13" or pins.uv != "0.10.8" or pins.github_runner != "ubuntu-24.04":
        raise PrerequisiteError("runtime or runner pin changed without a contract revision")


def validate_pins(pins: E2EPinManifest = E2E_PINS) -> None:
    """Reject mutable refs and malformed SHA-256 inputs."""
    if not isinstance(pins, E2EPinManifest):
        raise PrerequisiteError("pin manifest must be an immutable E2EPinManifest")
    if not pins.__dataclass_params__.frozen:  # type: ignore[attr-defined]
        raise PrerequisiteError("pin manifest must be frozen")
    _validate_pin_values(pins)


def budget_for(tier: Tier, cache_class: CacheClass) -> PhaseBudget:
    try:
        return PHASE_BUDGETS[(tier, cache_class)]
    except KeyError as error:
        raise ValueError(f"unsupported budget selection: {tier}/{cache_class}") from error


def classify_cache(*, source_hit: bool, uv_hit: bool) -> CacheClass:
    """Partial cache hits remain cold until every required cache is present."""
    return "warm" if source_hit and uv_hit else "cold"


def prerequisite_manifest(
    checks: Mapping[str, bool], *, platform: str | None = None
) -> dict[str, object]:
    """Return deterministic machine-readable prerequisite state."""
    resolved_platform = validate_platform(platform) if platform is not None else current_platform()
    missing = sorted(name for name, available in checks.items() if not available)
    return {"platform": resolved_platform, "missing": missing, "ok": not missing}


def require_prerequisites(
    checks: Mapping[str, bool], *, platform: str | None = None
) -> dict[str, object]:
    manifest = prerequisite_manifest(checks, platform=platform)
    if not manifest["ok"]:
        raise PrerequisiteError(json.dumps(manifest, sort_keys=True))
    return manifest


def pin_manifest_dict(pins: E2EPinManifest = E2E_PINS) -> dict[str, object]:
    validate_pins(pins)
    return asdict(pins)
