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


class WireNestedValue(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """Representative nested value used to pin the document wire shape."""

    label: str
    enabled: bool | None = None


class WireCreateValue(msgspec.Struct, frozen=True, tag="create"):
    name: str
    count: int = 0


class WireDeleteValue(msgspec.Struct, frozen=True, tag="delete"):
    name: str


class WireOperationDocument(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """Nested, tagged, defaulted, nullable and alias-capable wire fixture."""

    nested: WireNestedValue
    action: WireCreateValue | WireDeleteValue
    alias: str | None = None


def _concrete_model(name: str, base: type[msgspec.Struct]) -> type[msgspec.Struct]:
    """Create a named wire type without importing an operation domain.

    The built-in inventory is metadata-only, but its public schema still needs
    finite operation references.  Subclassing the common envelope preserves
    the stable selector/status fields while preventing every operation from
    advertising the generic base type.
    """
    return type(name, (base,), {"__module__": __name__})


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
    click_path: tuple[str, ...] | None = None

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
    ("remote", "ls"): (("remote", "list"),),
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
    (("contract", "export"), "bounded-read-only", False, None),
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


def _operation_parameters(case: PublicLeafCase) -> tuple[OperationParameter, ...]:
    parameters = [
        OperationParameter("project", "string|null", default=None),
        OperationParameter("environment", "string|null", default=None),
    ]
    if case.requires_dry_run:
        parameters.append(OperationParameter("dry_run", "boolean", default=True))
    return tuple(parameters)


def _operation_models(case: PublicLeafCase) -> tuple[type[msgspec.Struct], ...]:
    stem = "".join(part.replace("-", " ").title().replace(" ", "") for part in case.path)
    return (
        _concrete_model(f"{stem}Request", OperationRequest),
        _concrete_model(f"{stem}Result", OperationResult),
        _concrete_model(f"{stem}Error", OperationErrorDetails),
    )


def builtin_bindings() -> tuple[OperationBinding, ...]:
    """Build the data-only built-in bindings in canonical inventory order."""
    rows = []
    for case in PUBLIC_LEAF_CASES:
        request_type, result_type, error_type = _operation_models(case)
        rows.append(
            OperationBinding(
                descriptor=OperationDescriptor(
                    operation_id=_operation_id(case.path),
                    canonical_path=case.path,
                    aliases=case.aliases,
                    request_type=request_type,
                    result_type=result_type,
                    error_types=(error_type,),
                    parameters=_operation_parameters(case),
                    transport={
                        "native-passthrough": OperationTransport.NATIVE_TTY,
                        "jsonl-stream": OperationTransport.JSONL_STREAM,
                        "rich-live": OperationTransport.SESSION,
                    }.get(case.classification, OperationTransport.DOCUMENT),
                    preview=case.requires_dry_run,
                    approval_required=case.classification == "mutating-or-spawning",
                ),
                sdk_primitive=case.sdk_primitive,
                click_path=case.path if case.sdk_primitive is None else None,
            )
        )
    return tuple(rows)


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
        """Validate canonical leaves and aliases in the composed Click tree."""
        leaves = click_leaf_commands(command)
        seen: set[str] = set()
        for path, leaf in leaves:
            try:
                binding = self.for_path(path)
            except KeyError as exc:
                raise ValueError(f"unknown Click leaf: {' '.join(path)}") from exc
            seen.add(binding.operation_id)
            aliases = getattr(leaf, "_odcli_aliases", None)
            if aliases is None:
                aliases = getattr(leaf, "aliases", ())
            if aliases is None:
                aliases = ()
            command_aliases = tuple((*path[:-1], alias) for alias in aliases)
            composed_paths = {path, *command_aliases}
            declared_paths = {binding.canonical_path, *binding.aliases}
            if composed_paths != declared_paths:
                raise ValueError(
                    f"alias inventory drift for {' '.join(binding.canonical_path)}: "
                    f"expected={sorted(declared_paths)!r}, actual={sorted(composed_paths)!r}"
                )
            if getattr(leaf, "callback", None) is None:
                raise ValueError(f"leaf has no callback: {' '.join(path)}")
        expected = {binding.operation_id for binding in self.bindings}
        if seen != expected:
            missing = sorted(expected - seen)
            raise ValueError(f"Click operation inventory drift: missing={missing!r}")
        self.validate_sdk_primitives()

    def validate_sdk_primitives(self) -> None:
        """Reject stale SDK references and accidental CLI-only placeholders."""
        expected = {case.path: case for case in PUBLIC_LEAF_CASES}
        for binding in self.bindings:
            case = expected.get(binding.canonical_path)
            if case is None:
                continue
            if case.sdk_primitive != binding.sdk_primitive:
                raise ValueError(
                    f"SDK primitive drift for {' '.join(binding.canonical_path)}: "
                    f"expected={case.sdk_primitive!r}, actual={binding.sdk_primitive!r}"
                )
            if case.sdk_primitive is None and binding.click_path != case.path:
                raise ValueError(
                    f"CLI-only binding is not tied to its leaf: {binding.operation_id}"
                )

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
        if not binding.sdk_primitive and binding.factory is None and binding.click_path is None:
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


def click_leaf_commands(
    command: Any, prefix: tuple[str, ...] = ()
) -> tuple[tuple[tuple[str, ...], Any], ...]:
    """Return canonical leaves with their composed Click command objects."""
    commands = getattr(command, "commands", None)
    if not isinstance(commands, Mapping):
        return ((prefix, command),)
    return tuple(
        leaf
        for name, child in commands.items()
        for leaf in click_leaf_commands(child, (*prefix, str(name)))
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


def build_registry(
    bindings: Iterable[OperationBinding] | None = None,
    providers: Iterable[Any] = (),
) -> OperationRegistry:
    """Return the validated core registry without importing provider domains."""
    rows = list(builtin_bindings() if bindings is None else tuple(bindings))
    for provider in providers:
        rows.extend(provider_bindings(provider))
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
    """Reject any public descriptor/schema drift unless the version advances."""
    if previous.get("contract_version") != current.get("contract_version"):
        return
    for key in ("entry_point_group", "envelope"):
        if previous.get(key) != current.get(key):
            raise ValueError(f"breaking contract change in bundle: {key}")
    if previous.get("schemas") != current.get("schemas"):
        raise ValueError("breaking contract change in bundle: schemas")
    old_operations = {item["id"]: item for item in previous.get("operations", ())}
    new_operations = {item["id"]: item for item in current.get("operations", ())}
    removed = sorted(set(old_operations) - set(new_operations))
    if removed:
        raise ValueError(f"breaking contract change removed operations: {removed!r}")
    for operation_id in sorted(old_operations):
        old = old_operations[operation_id]
        new = new_operations[operation_id]
        keys = set(old) | set(new)
        for key in sorted(keys):
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
        "WireCreateValue": WireCreateValue,
        "WireDeleteValue": WireDeleteValue,
        "WireNestedValue": WireNestedValue,
        "WireOperationDocument": WireOperationDocument,
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
                "implementation": (
                    {"kind": "sdk-primitive", "reference": binding.sdk_primitive}
                    if binding.sdk_primitive is not None
                    else {"kind": "click-callback", "path": list(descriptor.canonical_path)}
                ),
            }
        )
    return {
        "contract_version": CONTRACT_VERSION,
        "entry_point_group": ENTRY_POINT_GROUP,
        "envelope": {
            "name": "OutputDocument",
            "schema_version": 1,
            "wire_fixture": "WireOperationDocument",
        },
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
    "WireCreateValue",
    "WireDeleteValue",
    "WireNestedValue",
    "WireOperationDocument",
    "assert_compatible_bundle",
    "assert_compatible_contract",
    "build_registry",
    "builtin_bindings",
    "click_leaf_commands",
    "click_leaf_paths",
    "contract_bundle",
    "contract_bytes",
    "provider_bindings",
    "validate_public_inventory",
    "wire_projection",
    "wire_schema",
]
