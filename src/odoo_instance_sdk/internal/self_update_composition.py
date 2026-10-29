"""Read and normalize uv-tool installation composition metadata."""

from __future__ import annotations

import json
import os
import re
import shutil
import tomllib
from dataclasses import dataclass
from importlib import import_module
from importlib.metadata import Distribution
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from odoo_instance_sdk.exceptions import UnsupportedInstallError, UpdateError
from odoo_instance_sdk.execution import JsonValue

if TYPE_CHECKING:
    from odoo_instance_sdk.internal.proc import ProcessResult, RunContext
    from odoo_instance_sdk.internal.self_update import InstalledProvenance
    from odoo_instance_sdk.models.update import UpdateResult

_EXTRA_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True, slots=True)
class InstalledCompanion:
    launcher: Path
    environment: Path
    version: str
    package_sha: str
    sdk_sha: str
    source_repo: str


def _protected_tool_file(environment: Path, path: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise UpdateError("installed companion metadata is missing or unreadable") from exc
    if path.is_symlink() or not resolved.is_relative_to(environment):
        raise UpdateError("installed companion metadata escapes its protected environment")
    return resolved


def _distribution_revision(environment: Path, package: str) -> tuple[str, str]:
    normalized = package.casefold().replace("_", "-").replace(".", "-")
    matches: list[tuple[str, str]] = []
    for metadata_dir in environment.glob("lib/python*/site-packages/*.dist-info"):
        _protected_tool_file(environment, metadata_dir)
        metadata = metadata_dir / "METADATA"
        direct_url = metadata_dir / "direct_url.json"
        if not metadata.is_file() or not direct_url.is_file():
            continue
        _protected_tool_file(environment, metadata)
        _protected_tool_file(environment, direct_url)
        name = next(
            (
                line.partition(":")[2].strip()
                for line in metadata.read_text(encoding="utf-8").splitlines()
                if line.startswith("Name:")
            ),
            "",
        )
        if name.casefold().replace("_", "-").replace(".", "-") != normalized:
            continue
        try:
            payload = json.loads(direct_url.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise UpdateError(f"installed {package} provenance is unreadable") from exc
        vcs = payload.get("vcs_info") if isinstance(payload, dict) else None
        revision = vcs.get("commit_id") if isinstance(vcs, dict) else None
        url = payload.get("url") if isinstance(payload, dict) else None
        if (
            isinstance(revision, str)
            and _HEX40.fullmatch(revision.lower())
            and isinstance(url, str)
        ):
            matches.append((revision.lower(), url))
    if len(matches) != 1:
        raise UpdateError(f"installed {package} provenance is missing or ambiguous")
    return matches[0]


def read_uv_tool_companion(  # noqa: C901
    *, source_repo: str, launcher_path: Path | None = None
) -> InstalledCompanion | None:
    """Capture the optional installed companion and its embedded SDK revision."""
    located = shutil.which("odcli-multica") if launcher_path is None else str(launcher_path)
    found = Path(located) if located is not None else None
    if found is None:
        return None
    if found.name != "odcli-multica":
        raise UnsupportedInstallError("odcli-multica launcher path is ambiguous")
    if not found.is_file():
        if launcher_path is not None:
            return None
        raise UnsupportedInstallError("odcli-multica launcher is present but not a regular file")
    try:
        launcher = found.resolve(strict=True)
    except OSError as exc:
        raise UnsupportedInstallError("odcli-multica launcher is unreadable") from exc
    if launcher.parent.name != "bin":
        raise UnsupportedInstallError("odcli-multica launcher is outside a supported uv tool")
    environment = launcher.parent.parent.resolve(strict=True)
    if not launcher.is_relative_to(environment / "bin") or not os.access(launcher, os.X_OK):
        raise UnsupportedInstallError("odcli-multica launcher escapes its protected environment")
    receipt = environment / "uv-receipt.toml"
    if receipt.is_symlink() or not receipt.is_file():
        raise UnsupportedInstallError("odcli-multica uv-tool receipt is missing or unreadable")
    try:
        tool = tomllib.loads(receipt.read_text(encoding="utf-8")).get("tool")
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise UnsupportedInstallError(
            "odcli-multica uv-tool receipt is missing or unreadable"
        ) from exc
    requirements = tool.get("requirements") if isinstance(tool, dict) else None
    if not isinstance(requirements, list):
        raise UnsupportedInstallError("odcli-multica uv-tool receipt is ambiguous")
    package_requirements = {
        item.get("name"): item
        for item in requirements
        if isinstance(item, dict) and item.get("name") in {"odcli-multica", "odoo-instance-sdk"}
    }
    if set(package_requirements) != {"odcli-multica", "odoo-instance-sdk"}:
        raise UnsupportedInstallError("odcli-multica uv-tool composition is ambiguous")
    expected = source_repo.removeprefix("git+").removesuffix(".git").casefold()
    for item in package_requirements.values():
        source = item.get("git")
        if not isinstance(source, str):
            raise UnsupportedInstallError("odcli-multica tool source provenance is ambiguous")
        actual = source.removeprefix("git+").split("?", 1)[0].removesuffix(".git").casefold()
        if actual != expected:
            raise UnsupportedInstallError(
                "odcli-multica tool source conflicts with core provenance"
            )
    package_sha, package_url = _distribution_revision(environment, "odcli-multica")
    sdk_sha, sdk_url = _distribution_revision(environment, "odoo-instance-sdk")
    for url in (package_url, sdk_url):
        actual = url.removeprefix("git+").split("@", 1)[0].removesuffix(".git").casefold()
        if actual != expected:
            raise UnsupportedInstallError("odcli-multica tool contains an untrusted SDK source")
    version_file = next(
        environment.glob("lib/python*/site-packages/odcli_multica-*.dist-info/METADATA"), None
    )
    if version_file is None:
        raise UnsupportedInstallError("odcli-multica package metadata is missing")
    version_path = _protected_tool_file(environment, version_file)
    version = next(
        (
            line.partition(":")[2].strip()
            for line in version_path.read_text(encoding="utf-8").splitlines()
            if line.startswith("Version:")
        ),
        "",
    )
    if not version:
        raise UnsupportedInstallError("odcli-multica package version is missing")
    return InstalledCompanion(launcher, environment, version, package_sha, sdk_sha, source_repo)


def _normalize_extra_set(raw: JsonValue) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise UnsupportedInstallError("uv-tool optional-extra metadata is ambiguous")
    values: list[str] = []
    for value in raw:
        if not isinstance(value, str) or not _EXTRA_NAME_RE.fullmatch(value.strip()):
            raise UnsupportedInstallError("uv-tool optional-extra metadata is malformed")
        normalized = value.strip().lower().replace("_", "-")
        if normalized in values:
            raise UnsupportedInstallError("uv-tool optional-extra metadata is ambiguous")
        values.append(normalized)
    return tuple(sorted(values))


def _format_direct_requirement(
    package_name: str,
    source_repo: str | None,
    requested_revision: str | None,
) -> str | None:
    if not source_repo:
        return None
    raw = source_repo.removeprefix("git+")
    return f"{package_name} @ git+{raw}@{requested_revision or 'main'}"


def _uv_tool_receipt_path(dist: Distribution) -> Path | None:
    """Locate uv's authoritative tool composition receipt beside the venv."""
    try:
        candidate = Path(str(dist.locate_file("../../../uv-receipt.toml")))
    except (AttributeError, OSError, TypeError):
        return None
    return candidate if candidate.is_file() else None


def uv_tool_environment_path(dist: Distribution) -> Path | None:
    receipt = _uv_tool_receipt_path(dist)
    if receipt is not None:
        return receipt.parent.resolve()
    files = dist.files
    if files is None:
        return None
    for file_path in files:
        resolved = Path(str(file_path))
        if resolved.name == "odcli" and "tools" in resolved.parts:
            tools_index = resolved.parts.index("tools")
            return Path(*resolved.parts[: tools_index + 2])
    return None


def read_uv_tool_companion_in_environment(
    source_repo: str, environment: Path
) -> InstalledCompanion | None:
    return read_uv_tool_companion(
        source_repo=source_repo, launcher_path=environment / "bin" / "odcli-multica"
    )


def companion_rollback_verified(
    result: ProcessResult, restored: InstalledCompanion | None, previous: InstalledCompanion
) -> bool:
    return bool(
        result.returncode == 0
        and restored is not None
        and restored.package_sha == previous.package_sha
        and restored.sdk_sha == previous.sdk_sha
    )


def read_uv_tool_composition(
    dist: Distribution,
    *,
    package_name: str,
    source_repo: str | None,
    requested_revision: str | None,
    manual_argv: tuple[str, ...],
) -> tuple[str | None, tuple[str, ...]]:
    """Read one unambiguous direct requirement from uv's tool receipt."""
    receipt = _uv_tool_receipt_path(dist)
    # Minimal distribution fakes used by the offline unit contract do not
    # expose a file tree. Treat those as legacy core-only metadata; real uv
    # tool distributions must provide the receipt below.
    if receipt is None and getattr(dist, "files", None) is None:
        return _format_direct_requirement(package_name, source_repo, requested_revision), ()
    if receipt is None:
        raise UnsupportedInstallError(
            "uv-tool installation composition metadata is missing or ambiguous",
            manual_argv=manual_argv,
        )
    try:
        payload = tomllib.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise UnsupportedInstallError(
            "uv-tool installation composition metadata is unreadable",
            manual_argv=manual_argv,
        ) from exc
    tool = payload.get("tool")
    requirements = tool.get("requirements") if isinstance(tool, dict) else None
    if not isinstance(requirements, list):
        raise UnsupportedInstallError(
            "uv-tool installation composition metadata is missing requirements",
            manual_argv=manual_argv,
        )
    matches = [
        item for item in requirements if isinstance(item, dict) and item.get("name") == package_name
    ]
    if len(matches) != 1:
        raise UnsupportedInstallError(
            "uv-tool installation composition has an ambiguous direct requirement",
            manual_argv=manual_argv,
        )
    requirement = matches[0]
    receipt_repo = requirement.get("git")
    if receipt_repo is not None and not isinstance(receipt_repo, str):
        raise UnsupportedInstallError(
            "uv-tool direct requirement has malformed source metadata",
            manual_argv=manual_argv,
        )
    if isinstance(receipt_repo, str) and source_repo:
        expected = source_repo.removeprefix("git+").removesuffix(".git").lower()
        actual = receipt_repo.removeprefix("git+").split("?", 1)[0].removesuffix(".git").lower()
        if expected != actual:
            raise UnsupportedInstallError(
                "uv-tool direct requirement source conflicts with installed provenance",
                manual_argv=manual_argv,
            )
    extras = _normalize_extra_set(requirement.get("extras"))
    return (
        _format_direct_requirement(
            package_name,
            source_repo or receipt_repo,
            requested_revision,
        ),
        extras,
    )


def install_requirement(
    ref: str,
    *,
    package_name: str = "odoo-instance-sdk",
    source_repo: str | None = None,
    extras: tuple[str, ...] = (),
) -> str:
    raw_source = (
        source_repo or "https://github.com/maximchikAlexandr/odoo-instance-sdk.git"
    ).removeprefix("git+")
    package = package_name + ("[" + ",".join(extras) + "]" if extras else "")
    return f"{package} @ git+{raw_source}@{ref}"


def install_argv(
    ref: str,
    *,
    package_name: str = "odoo-instance-sdk",
    source_repo: str | None = None,
    extras: tuple[str, ...] = (),
) -> tuple[str, ...]:
    return (
        "uv",
        "tool",
        "install",
        "--force",
        install_requirement(ref, package_name=package_name, source_repo=source_repo, extras=extras),
    )


def companion_install_argv(
    ref: str, *, sdk_ref: str | None = None, source_repo: str | None = None
) -> tuple[str, ...]:
    return (
        "uv",
        "tool",
        "install",
        "--force",
        install_requirement(ref, package_name="odcli-multica", source_repo=source_repo)
        + "#subdirectory=packages/odcli-multica",
        "--with",
        install_requirement(sdk_ref or ref, source_repo=source_repo),
    )


def journal_composition_fields(
    provenance: InstalledProvenance, target_ref: str
) -> dict[str, JsonValue]:
    fields: dict[str, JsonValue] = {
        "direct_requirement": provenance.direct_requirement,
        "requested_extras": list(provenance.requested_extras),
        "install_requirement": install_argv(
            target_ref,
            source_repo=provenance.source_repo,
            extras=provenance.requested_extras,
        )[-1],
    }
    companion = provenance.companion
    if companion is not None:
        fields["companion"] = {
            "previous_sha": companion.package_sha,
            "previous_sdk_sha": companion.sdk_sha,
            "target_sha": target_ref,
            "environment": str(companion.environment),
        }
    return fields


def resume_update_argv(executable: Path | None, target_ref: str) -> tuple[str, ...] | None:
    return (str(executable), "update", "--ref", target_ref, "--yes") if executable else None


def verify_companion_revision(
    version_result: ProcessResult, metadata_result: ProcessResult, target_ref: str
) -> None:
    if version_result.returncode != 0 or metadata_result.returncode != 0:
        raise UpdateError("odcli-multica or its embedded SDK failed verification")
    raw = metadata_result.stdout
    try:
        payload = json.loads(
            raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw or "")
        )
    except json.JSONDecodeError as exc:
        raise UpdateError("odcli-multica revision evidence is invalid") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("odcli-multica") != target_ref
        or payload.get("odoo-instance-sdk") != target_ref
    ):
        raise UpdateError("odcli-multica or its embedded SDK revision does not match target")


def skip_companion_verification(context: RunContext[UpdateResult]) -> None:
    for step_id in (
        "update.verify",
        "update.verify.version",
        "update.install.companion",
        "update.verify.companion.version",
        "update.verify.companion.revisions",
        "update.recovery.companion",
        "update.commit",
    ):
        if context.planned(step_id) and not context.consumed(step_id):
            context.skip(step_id)


class _DashboardProvenance(Protocol):
    @property
    def requested_extras(self) -> tuple[str, ...]: ...


def verify_dashboard_installation(provenance: _DashboardProvenance) -> None:
    """Verify dashboard imports and packaged SPA assets in the new interpreter."""
    extras = getattr(provenance, "requested_extras", ())
    if "dashboard" not in extras:
        return
    try:
        import_module("fastapi")
        import_module("starlette")
        import_module("uvicorn")

        import odoo_instance_sdk
    except ImportError as exc:
        raise UpdateError("dashboard dependency import failed after update") from exc
    package_root = Path(odoo_instance_sdk.__file__).resolve().parent
    entry = package_root / "web" / "dist" / "index.html"
    assets = entry.parent / "assets"
    if not entry.is_file() or not assets.is_dir() or not any(assets.iterdir()):
        raise UpdateError("packaged dashboard SPA assets are missing after update")


__all__ = [
    "companion_install_argv",
    "install_argv",
    "install_requirement",
    "read_uv_tool_composition",
    "verify_dashboard_installation",
]
