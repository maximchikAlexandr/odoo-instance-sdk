"""Stable metadata contracts shared by the local machine-operation boundary.

This module deliberately contains metadata and DTO projection only. Importing
it does not resolve a project, open a catalogue, or import an operation domain.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

import msgspec

CONTRACT_VERSION = 1
ENTRY_POINT_GROUP = "odoo_instance_sdk.operations"


class OperationTransport(StrEnum):
    """The one transport an operation binding owns."""

    DOCUMENT = "finite-document"
    SESSION = "bounded-jsonl-session"
    NATIVE_TTY = "native-tty"
    JSONL_STREAM = "jsonl-stream"
    INTERACTIVE = "interactive"


class OperationRequest(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Common selector envelope for finite built-ins."""

    project: str | None = None
    environment: str | None = None


class OperationResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Small metadata result used by unadapted built-in inventory rows."""

    status: str = "ok"


class OperationErrorDetails(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Sanitized, finite error detail projection."""

    code: str
    message: str


@dataclass(frozen=True, slots=True)
class OperationParameter:
    name: str
    schema: str
    required: bool = False
    default: Any = None


@dataclass(frozen=True, slots=True)
class OperationError:
    code: str
    message: str
    details_schema: str | None = None


@dataclass(frozen=True, slots=True)
class OperationDescriptor:
    """Frozen, serializable-by-projection description of one operation."""

    operation_id: str
    canonical_path: tuple[str, ...]
    aliases: tuple[tuple[str, ...], ...] = ()
    provider: str = "core"
    contract_version: int = CONTRACT_VERSION
    request_type: type[msgspec.Struct] = OperationRequest
    result_type: type[msgspec.Struct] = OperationResult
    error_types: tuple[type[msgspec.Struct], ...] = (OperationErrorDetails,)
    parameters: tuple[OperationParameter, ...] = ()
    context_policy: str = "invocation-scoped"
    transport: OperationTransport = OperationTransport.DOCUMENT
    preview: bool = False
    approval_required: bool = False
    cancellation: str = "not-applicable"
    exit_mapping: str = "envelope-v1"
    domain_status_field: str | None = None

    def __post_init__(self) -> None:
        if not self.operation_id or "." not in self.operation_id:
            raise ValueError("operation_id must be a stable namespaced identifier")
        if not self.canonical_path or any(not part for part in self.canonical_path):
            raise ValueError("canonical_path must contain non-empty Click path parts")
        if self.contract_version != CONTRACT_VERSION:
            raise ValueError(f"unsupported operation contract version: {self.contract_version}")
        if self.approval_required and not self.preview:
            raise ValueError("approval_required requires preview")


OperationFactory = Callable[..., Any]


@dataclass(frozen=True, slots=True)
class OperationBinding:
    """One descriptor plus its implementation reference and optional factory."""

    descriptor: OperationDescriptor
    sdk_primitive: str | None = None
    factory: OperationFactory | None = None

    @property
    def operation_id(self) -> str:
        return self.descriptor.operation_id

    @property
    def canonical_path(self) -> tuple[str, ...]:
        return self.descriptor.canonical_path

    @property
    def aliases(self) -> tuple[tuple[str, ...], ...]:
        return self.descriptor.aliases


class OperationProvider(Protocol):
    """Entry-point provider protocol for a finite complete binding tuple."""

    contract_version: int

    def __call__(self) -> tuple[OperationBinding, ...]: ...


@dataclass(frozen=True, slots=True)
class PublicLeafCase:
    """Production-owned characterization row for one canonical Click leaf."""

    path: tuple[str, ...]
    classification: str
    requires_dry_run: bool
    sdk_primitive: str | None = None
    cli_only_reason: str | None = None
    aliases: tuple[tuple[str, ...], ...] = ()

    @property
    def is_bounded(self) -> bool:
        return self.classification in {
            "bounded-read-only",
            "process-previewable-read-only",
            "mutating-or-spawning",
        }


_ALIASES: dict[tuple[str, ...], tuple[tuple[str, ...], ...]] = {
    ("env", "create"): (("env", "checkout"),),
    ("env", "ls"): (("env", "list"),),
    ("env", "rm"): (("env", "remove"),),
    ("backup", "ls"): (("backup", "list"),),
    ("backup", "inspect"): (("backup", "show"),),
    ("backup", "rm"): (("backup", "delete"),),
    ("db", "ls"): (("db", "list"),),
    ("db", "rm"): (("db", "drop"),),
    ("postgres", "ps"): (("postgres", "status"),),
    ("resource", "ls"): (("resource", "list"),),
    ("module", "ls"): (("module", "list"),),
}


# This is intentionally a data-only projection. It replaces the test-owned
# table as the runtime authority without importing command callbacks.
_LEAF_ROWS: tuple[tuple[tuple[str, ...], str, bool, str | None], ...] = (
    (("init",), "mutating-or-spawning", True, "init_project_command"),
    (("doctor",), "bounded-read-only", False, "run_doctor"),
    (("stop",), "mutating-or-spawning", True, "OdooInstance.stop_runtime_command"),
    (("resource", "ls"), "bounded-read-only", False, None),
    (("resource", "doctor"), "bounded-read-only", False, None),
    (("env", "create"), "mutating-or-spawning", True, "EnvironmentResource.checkout_command"),
    (("env", "ls"), "bounded-read-only", False, "EnvironmentResource.checkout_inventory_command"),
    (("env", "path"), "bounded-read-only", False, None),
    (("env", "show"), "bounded-read-only", False, None),
    (("env", "rm"), "mutating-or-spawning", True, "EnvironmentResource.remove_command"),
    (("env", "sync"), "mutating-or-spawning", True, "EnvironmentResource.sync_python_command"),
    (("remote", "ls"), "bounded-read-only", False, "list_remote_sources"),
    (("remote", "add"), "mutating-or-spawning", True, "configure_remote_source_command"),
    (("remote", "update"), "mutating-or-spawning", True, "configure_remote_source_command"),
    (("remote", "remove"), "mutating-or-spawning", True, "remove_remote_source_command"),
    (("backup", "ls"), "bounded-read-only", False, None),
    (("backup", "retention"), "mutating-or-spawning", True, "BackupResource.set_retention_command"),
    (("backup", "pin"), "mutating-or-spawning", True, "BackupResource.set_pinned_command"),
    (("backup", "unpin"), "mutating-or-spawning", True, "BackupResource.set_pinned_command"),
    (("backup", "prune"), "mutating-or-spawning", True, "BackupResource.prune_command"),
    (("backup", "inspect"), "bounded-read-only", False, "BackupResource.inspect_command"),
    (("backup", "validate"), "bounded-read-only", False, "BackupResource.validate_command"),
    (("backup", "rm"), "mutating-or-spawning", True, "BackupResource.delete_command"),
    (
        ("db", "refresh"),
        "mutating-or-spawning",
        True,
        "EnvironmentResource.refresh_database_command",
    ),
    (
        ("db", "restore"),
        "mutating-or-spawning",
        True,
        "EnvironmentResource.refresh_database_command",
    ),
    (("db", "ls"), "bounded-read-only", False, "DatabaseResource.list_inventory_command"),
    (
        ("db", "reset-admin-password"),
        "mutating-or-spawning",
        True,
        "DatabaseResource.reset_admin_password_command",
    ),
    (("db", "rm"), "mutating-or-spawning", True, None),
    (("eval",), "process-previewable-read-only", True, "eval_expression_command"),
    (("exec",), "mutating-or-spawning", True, "exec_script_command"),
    (("test",), "process-previewable-read-only", True, "run_odoo_tests_command"),
    (("module", "ls"), "process-previewable-read-only", True, "list_modules_command"),
    (("module", "update"), "mutating-or-spawning", True, "ModuleResource.update_command"),
    (("module", "test"), "mutating-or-spawning", True, "run_odoo_tests_command"),
    (("module", "info"), "bounded-read-only", False, "ModuleResource.info"),
    (("module", "where"), "bounded-read-only", False, "ModuleResource.where"),
    (("module", "deps"), "bounded-read-only", False, "ModuleResource.dependencies"),
    (
        ("module", "install-order"),
        "process-previewable-read-only",
        True,
        "ModuleResource.install_order_command",
    ),
    (("translations", "export"), "mutating-or-spawning", True, "export_translations_command"),
    (("deps", "verify"), "process-previewable-read-only", True, "verify_deps_command"),
    (("vscode", "generate"), "mutating-or-spawning", True, None),
    (
        ("postgres", "approve-image"),
        "mutating-or-spawning",
        True,
        "PostgresCluster.approve_image_command",
    ),
    (("postgres", "ps"), "bounded-read-only", False, "PostgresCluster.status_command"),
    (("postgres", "up"), "mutating-or-spawning", True, "PostgresCluster.ensure_running_command"),
    (("postgres", "stop"), "mutating-or-spawning", True, "PostgresCluster.stop_command"),
    (("db", "locks"), "bounded-read-only", False, "DatabaseResource.locks_command"),
    (("db", "stats"), "bounded-read-only", False, "DatabaseResource.stats_command"),
    (("db", "bloat"), "bounded-read-only", False, "DatabaseResource.bloat_command"),
    (
        ("db", "init-monitoring"),
        "mutating-or-spawning",
        True,
        "DatabaseResource.init_monitoring_command",
    ),
    (("psql",), "native-passthrough", True, None),
    (("run",), "native-passthrough", True, None),
    (("logs",), "jsonl-stream", False, None),
    (("shell",), "native-passthrough", True, None),
    (("monitor",), "native-passthrough", False, None),
    (("git", "commit"), "mutating-or-spawning", True, "GitResource.commit_command"),
    (("git", "check"), "bounded-read-only", False, "GitResource.check_command"),
    (("git", "absorb"), "mutating-or-spawning", True, "GitResource.absorb_command"),
    (("git", "sync"), "mutating-or-spawning", True, "GitResource.sync_command"),
    (("ps",), "bounded-read-only", False, "EnvironmentMonitor.processes_command"),
    (("bug-report", "init"), "mutating-or-spawning", True, "bug_report_init_command"),
    (("bug-report", "submit"), "mutating-or-spawning", True, "bug_report_submit_command"),
    (("update",), "mutating-or-spawning", True, "update_command"),
)


PUBLIC_LEAF_CASES: tuple[PublicLeafCase, ...] = tuple(
    PublicLeafCase(
        path=path,
        classification=classification,
        requires_dry_run=requires_dry_run,
        sdk_primitive=sdk_primitive,
        cli_only_reason=("CLI-only composition projection" if sdk_primitive is None else None),
        aliases=_ALIASES.get(path, ()),
    )
    for path, classification, requires_dry_run, sdk_primitive in _LEAF_ROWS
)


def _operation_id(path: Sequence[str]) -> str:
    return "odcli." + ".".join(path).replace("-", "_")


def builtin_bindings() -> tuple[OperationBinding, ...]:
    """Build the data-only built-in bindings in canonical inventory order."""
    return tuple(
        OperationBinding(
            descriptor=OperationDescriptor(
                operation_id=_operation_id(case.path),
                canonical_path=case.path,
                aliases=case.aliases,
                transport={
                    "native-passthrough": OperationTransport.NATIVE_TTY,
                    "jsonl-stream": OperationTransport.JSONL_STREAM,
                    "rich-live": OperationTransport.SESSION,
                }.get(case.classification, OperationTransport.DOCUMENT),
                preview=case.requires_dry_run,
                approval_required=case.classification == "mutating-or-spawning",
            ),
            sdk_primitive=case.sdk_primitive or "cli." + ".".join(case.path),
        )
        for case in PUBLIC_LEAF_CASES
    )


class OperationRegistry:
    """Validated immutable lookup of operation bindings."""

    def __init__(self, bindings: Iterable[OperationBinding]) -> None:
        rows = tuple(bindings)
        _validate_bindings(rows)
        self.bindings = rows
        self._by_id = {row.operation_id: row for row in rows}
        self._by_path = {path: row for row in rows for path in (row.canonical_path, *row.aliases)}

    def get(self, operation_id: str) -> OperationBinding:
        try:
            return self._by_id[operation_id]
        except KeyError as exc:
            raise KeyError(f"unknown operation id: {operation_id}") from exc

    def for_path(self, path: Sequence[str]) -> OperationBinding:
        try:
            return self._by_path[tuple(path)]
        except KeyError as exc:
            raise KeyError(f"unknown operation path: {' '.join(path)}") from exc

    def validate_paths(self, paths: Iterable[Sequence[str]]) -> None:
        actual = {tuple(path) for path in paths}
        expected = {row.canonical_path for row in self.bindings}
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            raise ValueError(f"leaf inventory drift: missing={missing!r}, extra={extra!r}")

    def validate_click_tree(self, command: Any) -> None:
        """Validate the composed Click leaf set without importing callbacks here."""
        self.validate_paths(click_leaf_paths(command))

    def bundle(self) -> dict[str, Any]:
        return contract_bundle(self.bindings)


def _validate_bindings(bindings: tuple[OperationBinding, ...]) -> None:
    ids: set[str] = set()
    paths: dict[tuple[str, ...], str] = {}
    for binding in bindings:
        descriptor = binding.descriptor
        if descriptor.operation_id in ids:
            raise ValueError(f"duplicate operation id: {descriptor.operation_id}")
        ids.add(descriptor.operation_id)
        if not binding.sdk_primitive and binding.factory is None:
            raise ValueError(f"incomplete binding: {descriptor.operation_id} has no implementation")
        for path in (descriptor.canonical_path, *descriptor.aliases):
            owner = paths.get(path)
            if owner is not None:
                raise ValueError(f"duplicate operation path: {' '.join(path)} ({owner})")
            paths[path] = descriptor.operation_id


def click_leaf_paths(command: Any, prefix: tuple[str, ...] = ()) -> tuple[tuple[str, ...], ...]:
    """Return canonical Click leaves in deterministic composition order."""
    commands = getattr(command, "commands", None)
    if not isinstance(commands, Mapping):
        return (prefix,)
    return tuple(
        leaf
        for name, child in commands.items()
        for leaf in click_leaf_paths(child, (*prefix, str(name)))
    )


def validate_public_inventory(
    cases: Iterable[PublicLeafCase],
    *,
    registered_paths: Iterable[Sequence[str]] | None = None,
) -> tuple[PublicLeafCase, ...]:
    """Reject missing/stale/duplicate inventory rows before registry use."""
    rows = tuple(cases)
    paths = tuple(row.path for row in rows)
    if len(paths) != len(set(paths)):
        raise ValueError("PUBLIC_LEAF_CASES contains duplicate paths")
    if registered_paths is not None:
        actual = {tuple(path) for path in registered_paths}
        expected = set(paths)
        if actual != expected:
            raise ValueError(
                f"leaf inventory drift: missing={sorted(actual - expected)!r}, "
                f"extra={sorted(expected - actual)!r}"
            )
    for row in rows:
        if not row.path or any(not part for part in row.path):
            raise ValueError("leaf inventory contains an empty path")
        if bool(row.sdk_primitive) == bool(row.cli_only_reason):
            raise ValueError(
                f"leaf {' '.join(row.path)} requires exactly one implementation or CLI-only reason"
            )
    return rows


def build_registry(bindings: Iterable[OperationBinding] | None = None) -> OperationRegistry:
    """Return the validated core registry without importing provider domains."""
    rows = builtin_bindings() if bindings is None else tuple(bindings)
    return OperationRegistry(rows)


def provider_bindings(provider: Any) -> tuple[OperationBinding, ...]:
    """Read and strictly validate one installed provider's finite bindings."""
    version = getattr(provider, "contract_version", CONTRACT_VERSION)
    if version != CONTRACT_VERSION:
        raise ValueError(f"unsupported provider contract version: {version}")
    if callable(provider):
        rows = provider()
    elif callable(getattr(provider, "bindings", None)):
        rows = provider.bindings()
    elif callable(getattr(provider, "provide", None)):
        rows = provider.provide()
    else:
        raise TypeError("operation provider has no bindings callable")
    if not isinstance(rows, tuple):
        raise TypeError("operation provider must return a finite tuple")
    for row in rows:
        if not isinstance(row, OperationBinding) or row.factory is None:
            raise ValueError("operation provider returned an incomplete binding")
        if not row.descriptor.provider or row.descriptor.provider == "core":
            raise ValueError("provider bindings must identify their provider")
    _validate_bindings(rows)
    return rows


def _type_schema(model: type[msgspec.Struct]) -> dict[str, Any]:
    return msgspec.json.schema(model, ref_template="#/$defs/{name}")


def wire_projection(value: msgspec.Struct) -> Any:
    """Return the same JSON-safe projection used by the runtime encoder."""
    return msgspec.to_builtins(value)


def wire_schema(model: type[msgspec.Struct]) -> dict[str, Any]:
    """Project one concrete DTO through msgspec's actual wire metadata."""
    return _type_schema(model)


def assert_compatible_bundle(previous: Mapping[str, Any], current: Mapping[str, Any]) -> None:
    """Reject removals or schema changes unless the contract version advances."""
    if previous.get("contract_version") != current.get("contract_version"):
        return
    old_operations = {item["id"]: item for item in previous.get("operations", ())}
    new_operations = {item["id"]: item for item in current.get("operations", ())}
    removed = sorted(set(old_operations) - set(new_operations))
    if removed:
        raise ValueError(f"breaking contract change removed operations: {removed!r}")
    for operation_id in sorted(old_operations):
        old = old_operations[operation_id]
        new = new_operations[operation_id]
        for key in ("request_schema", "result_schema", "error_schemas", "transport"):
            if old.get(key) != new.get(key):
                raise ValueError(f"breaking contract change in {operation_id}: {key}")


def assert_compatible_contract(previous: Mapping[str, Any], current: Mapping[str, Any]) -> None:
    """Compatibility-fixture spelling retained for callers and tooling."""
    assert_compatible_bundle(previous, current)


def contract_bundle(bindings: Iterable[OperationBinding] | None = None) -> dict[str, Any]:
    """Build a deterministic metadata-only contract bundle."""
    registry = build_registry(bindings)
    models: dict[str, type[msgspec.Struct]] = {
        "OperationRequest": OperationRequest,
        "OperationResult": OperationResult,
        "OperationErrorDetails": OperationErrorDetails,
    }
    from odoo_instance_sdk.models.deps import DepsMissingImport, DepsVerifyResult

    models.update(
        {
            "DepsMissingImport": DepsMissingImport,
            "DepsVerifyResult": DepsVerifyResult,
        }
    )
    for binding in registry.bindings:
        for model in (
            binding.descriptor.request_type,
            binding.descriptor.result_type,
            *binding.descriptor.error_types,
        ):
            models.setdefault(model.__name__, model)

    schemas = {name: _type_schema(models[name]) for name in sorted(models)}
    operations = []
    for binding in registry.bindings:
        descriptor = binding.descriptor
        operations.append(
            {
                "id": descriptor.operation_id,
                "provider": descriptor.provider,
                "contract_version": descriptor.contract_version,
                "canonical_path": list(descriptor.canonical_path),
                "aliases": [list(path) for path in descriptor.aliases],
                "request_schema": descriptor.request_type.__name__,
                "result_schema": descriptor.result_type.__name__,
                "error_schemas": [model.__name__ for model in descriptor.error_types],
                "parameters": [
                    {
                        "name": item.name,
                        "schema": item.schema,
                        "required": item.required,
                        "default": item.default,
                    }
                    for item in descriptor.parameters
                ],
                "context_policy": descriptor.context_policy,
                "transport": descriptor.transport.value,
                "preview": descriptor.preview,
                "approval_required": descriptor.approval_required,
                "cancellation": descriptor.cancellation,
                "exit_mapping": descriptor.exit_mapping,
                "domain_status_field": descriptor.domain_status_field,
            }
        )
    return {
        "contract_version": CONTRACT_VERSION,
        "entry_point_group": ENTRY_POINT_GROUP,
        "envelope": {"name": "OutputDocument", "schema_version": 1},
        "operations": operations,
        "schemas": schemas,
    }


def contract_bytes(bindings: Iterable[OperationBinding] | None = None) -> bytes:
    return json.dumps(
        contract_bundle(bindings),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


__all__ = [
    "CONTRACT_VERSION",
    "ENTRY_POINT_GROUP",
    "PUBLIC_LEAF_CASES",
    "OperationBinding",
    "OperationDescriptor",
    "OperationError",
    "OperationErrorDetails",
    "OperationFactory",
    "OperationParameter",
    "OperationProvider",
    "OperationRegistry",
    "OperationRequest",
    "OperationResult",
    "OperationTransport",
    "PublicLeafCase",
    "assert_compatible_bundle",
    "assert_compatible_contract",
    "build_registry",
    "builtin_bindings",
    "click_leaf_paths",
    "contract_bundle",
    "contract_bytes",
    "provider_bindings",
    "validate_public_inventory",
    "wire_projection",
    "wire_schema",
]
