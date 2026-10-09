from __future__ import annotations

from typing import Literal

import msgspec

from odoo_instance_sdk.models._literals import ModuleJsonValue


class Module(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """One safely discovered Odoo addon manifest."""

    name: str
    path: str
    manifest_path: str
    depends: tuple[str, ...] = ()
    manifest: dict[str, ModuleJsonValue] = {}
    shadowed_paths: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    repository: str | None = None
    repository_path: str | None = None
    dependency_details: tuple[ModuleDependency, ...] = ()
    installed_state: str | None = None
    installed_version: str | None = None
    changes: tuple[ModuleGitChange, ...] = ()
    shadowed: tuple[ModuleProvenance, ...] = ()

    @property
    def dependencies(self) -> tuple[str, ...]:
        return self.depends

    @property
    def version(self) -> str | None:
        value = self.manifest.get("version")
        return value if isinstance(value, str) else None

    @property
    def manifest_version(self) -> str | None:
        return self.version

    @property
    def code_path(self) -> str:
        return self.path

    @property
    def installed(self) -> bool | None:
        if self.installed_state is None:
            return None
        return self.installed_state == "installed"


class ModuleProvenance(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Filesystem provenance for one authoritative or shadowed candidate."""

    name: str
    path: str
    manifest_path: str
    repository: str | None = None
    repository_path: str | None = None


class ModuleGitChange(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """One related committed or working-tree Git change."""

    path: str
    kind: Literal["committed", "staged", "unstaged", "untracked"]
    status: str | None = None
    repository: str | None = None


class ModuleAvailability(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Availability of one independently collected module-context source."""

    state: Literal["available", "unavailable"]
    reason: str | None = None


class ModuleContext(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Read-only filesystem, Git, and selected-database module facts."""

    modules: tuple[Module, ...] = ()
    filesystem: ModuleAvailability = ModuleAvailability(state="available")
    git: ModuleAvailability = ModuleAvailability(state="unavailable", reason="not_collected")
    database: ModuleAvailability = ModuleAvailability(state="unavailable", reason="not_collected")
    warnings: tuple[str, ...] = ()

    @property
    def file_availability(self) -> ModuleAvailability:
        return self.filesystem

    @property
    def git_availability(self) -> ModuleAvailability:
        return self.git

    @property
    def database_availability(self) -> ModuleAvailability:
        return self.database


class ModuleDependency(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """A direct dependency and its resolved addon path, when present."""

    name: str
    path: str | None = None
    missing: bool = False


class ModuleDependencies(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    module: Module
    dependencies: tuple[ModuleDependency, ...] = ()


class ModuleInstallOrder(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    modules: tuple[str, ...]


class ModuleUpdatePlan(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Selection facts retained beside an immutable module update command."""

    modules: tuple[str, ...] = ()
    not_installed: tuple[str, ...] = ()
    base_source: str | None = None
    requested_base: str | None = None
    resolved_base: str | None = None
    merge_base: str | None = None
    head: str | None = None
    changed_files: tuple[str, ...] = ()
    ignored_paths: tuple[str, ...] = ()
    unmapped_paths: tuple[str, ...] = ()


class ModuleUpdateResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    modules: tuple[str, ...] = ()
    updated: tuple[str, ...] = ()
    not_installed: tuple[str, ...] = ()


# Descriptive compatibility names keep the public model vocabulary readable
# for callers that prefer the operation-specific spelling.
ModuleInfo = Module
ModuleDependencyResult = ModuleDependencies
ModuleInstallOrderResult = ModuleInstallOrder
ModuleContextResult = ModuleContext
ModuleChange = ModuleGitChange
