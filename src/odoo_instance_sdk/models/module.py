from __future__ import annotations

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

    @property
    def dependencies(self) -> tuple[str, ...]:
        return self.depends

    @property
    def version(self) -> str | None:
        value = self.manifest.get("version")
        return value if isinstance(value, str) else None


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
