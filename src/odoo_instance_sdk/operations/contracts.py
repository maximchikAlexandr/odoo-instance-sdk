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

from .inventory import build_public_leaf_cases

CONTRACT_VERSION = 1
ENTRY_POINT_GROUP = "odoo_instance_sdk.operations"


class OperationTransport(StrEnum):
    """The one transport an operation binding owns."""

    DOCUMENT = "finite-document"
    SESSION = "bounded-jsonl-session"
    NATIVE_TTY = "native-tty"
    JSONL_STREAM = "jsonl-stream"
    INTERACTIVE = "interactive"


_NO_DEFAULT = object()


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


@dataclass(frozen=True, slots=True)
class OperationParameter:
    name: str
    schema: str
    required: bool = False
    default: Any = _NO_DEFAULT


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
    request_type: type[msgspec.Struct] | None = OperationRequest
    result_type: type[msgspec.Struct] | None = OperationResult
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
        if self.transport in {
            OperationTransport.SESSION,
            OperationTransport.NATIVE_TTY,
            OperationTransport.JSONL_STREAM,
            OperationTransport.INTERACTIVE,
        } and any((self.request_type, self.result_type, self.error_types)):
            raise ValueError("unbounded operation cannot advertise a finite document payload")


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


PUBLIC_LEAF_CASES: tuple[PublicLeafCase, ...] = build_public_leaf_cases(PublicLeafCase)


def _operation_parameters(case: PublicLeafCase) -> tuple[OperationParameter, ...]:
    from .builtin_models import _operation_parameters as implementation

    return implementation(case)


def _request_type(
    case: PublicLeafCase, parameters: tuple[OperationParameter, ...]
) -> type[msgspec.Struct] | None:
    from .builtin_models import _request_type as implementation

    return implementation(case, parameters)


def _failure_type(case: PublicLeafCase) -> type[msgspec.Struct] | None:
    from .builtin_models import _failure_type as implementation

    return implementation(case)


def _result_type(case: PublicLeafCase) -> type[msgspec.Struct] | None:
    from .builtin_models import _result_type as implementation

    return implementation(case)


def _operation_id(path: Sequence[str]) -> str:
    return "odcli." + ".".join(path).replace("-", "_")


def _builtin_factory(case: PublicLeafCase) -> OperationFactory | None:  # noqa: C901
    """Create a lazy adapter for the existing SDK primitive seam.

    The adapter imports no domain modules during registry construction.  It
    only resolves the already-captured instance when a local invocation runs.
    """

    reference = case.sdk_primitive
    if reference is None and case.path != ("contract", "export"):
        return None

    def factory(request: msgspec.Struct | None, context: object) -> object:  # noqa: C901
        from odoo_instance_sdk.commands.context import OperationContext

        if not isinstance(context, OperationContext):
            raise TypeError("operation factory requires OperationContext")
        values = msgspec.to_builtins(request) if isinstance(request, msgspec.Struct) else {}
        if not isinstance(values, dict):
            raise TypeError("operation request must project to an object")
        if case.path == ("contract", "export"):
            import hashlib

            payload = contract_bytes()
            return {
                "contract_version": CONTRACT_VERSION,
                "entry_point_group": ENTRY_POINT_GROUP,
                "operation_count": len(PUBLIC_LEAF_CASES),
                "bundle_sha256": hashlib.sha256(payload).hexdigest(),
            }
        resolved = context.resolved
        if resolved is None:
            raise RuntimeError("operation context has no resolved runtime")
        instance = resolved.instance
        if reference == "eval_expression_command":
            from odoo_instance_sdk.internal.automation import eval_expression_command

            return eval_expression_command(
                instance,
                str(values["expression"]),
                commit=bool(values.get("commit", False)),
            )
        if reference == "exec_script_command":
            from odoo_instance_sdk.internal.automation import exec_script_command

            return exec_script_command(
                instance,
                str(values["script"]),
                tuple(str(item) for item in values.get("script_args", [])),
                commit=bool(values.get("commit", False)),
            )
        if reference == "list_modules_command":
            from odoo_instance_sdk.internal.automation import list_modules_command

            return list_modules_command(
                instance,
                tuple(str(item) for item in values.get("modules", [])),
                state=values.get("state"),
            )
        if reference == "run_odoo_tests_command":
            from odoo_instance_sdk.internal.automation import run_odoo_tests_command
            from odoo_instance_sdk.models.command import OdooTestSpec

            return run_odoo_tests_command(
                instance,
                OdooTestSpec(
                    modules=tuple(str(item) for item in values.get("modules", [])),
                    test_tags=str(values.get("test_tags", "/")),
                    reload_tests=bool(values.get("reload_tests", False)),
                    allow_empty=bool(values.get("allow_empty", False)),
                ),
                http_interface=context.runtime.http_interface,
                http_port=context.runtime.http_port,
            )
        if reference == "verify_deps_command":
            from odoo_instance_sdk.resources.deps import verify_deps_command

            return verify_deps_command(
                recorded_python=context.runtime.python_path,
                worktree_root=context.runtime.root,
            )
        target: object | None = None
        if reference is not None and reference.startswith("OdooInstance."):
            target = instance
        elif reference is not None and reference.startswith("DatabaseResource."):
            target = instance.databases
        elif reference is not None and reference.startswith("GitResource."):
            target = instance.git
        elif reference is not None and reference.startswith("ModuleResource."):
            target = instance.modules
        elif reference is not None and reference.startswith("PostgresCluster."):
            target = getattr(instance, "_postgres_cluster", None)
        if target is None or reference is None:
            raise RuntimeError(f"operation {case.path!r} is not available for this context")
        method = getattr(target, reference.rsplit(".", 1)[-1], None)
        if not callable(method):
            raise TypeError(f"SDK primitive is unavailable: {reference}")
        values.pop("output_format", None)
        values.pop("dry_run", None)
        values.pop("yes", None)
        values.pop("field_selection", None)
        if case.path == ("db", "ls"):
            return method(context.runtime.root, **values)
        if case.path == ("module", "ls"):
            return method(instance, **values)
        if case.path in {("module", "info"), ("module", "where"), ("module", "deps")}:
            return method(values.pop("module", None))
        return method(**values)

    return factory


def builtin_bindings() -> tuple[OperationBinding, ...]:
    """Build the data-only built-in bindings in canonical inventory order."""
    rows = []
    for case in PUBLIC_LEAF_CASES:
        parameters = _operation_parameters(case)
        request_type = _request_type(case, parameters)
        result_type = _result_type(case)
        error_type = _failure_type(case)
        transport = {
            "native-passthrough": OperationTransport.NATIVE_TTY,
            "jsonl-stream": OperationTransport.JSONL_STREAM,
            "rich-live": OperationTransport.SESSION,
        }.get(case.classification, OperationTransport.DOCUMENT)
        rows.append(
            OperationBinding(
                descriptor=OperationDescriptor(
                    operation_id=_operation_id(case.path),
                    canonical_path=case.path,
                    aliases=case.aliases,
                    request_type=request_type,
                    result_type=result_type,
                    error_types=() if error_type is None else (error_type,),
                    parameters=parameters,
                    transport=transport,
                    preview=case.requires_dry_run,
                    approval_required=case.classification == "mutating-or-spawning",
                ),
                sdk_primitive=case.sdk_primitive,
                factory=_builtin_factory(case),
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
            if model is not None:
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
                "request_schema": (
                    descriptor.request_type.__name__
                    if descriptor.request_type is not None
                    else None
                ),
                "result_schema": (
                    descriptor.result_type.__name__ if descriptor.result_type is not None else None
                ),
                "error_schemas": [model.__name__ for model in descriptor.error_types],
                "parameters": [
                    {
                        "name": item.name,
                        "schema": item.schema,
                        "required": item.required,
                        **({} if item.default is _NO_DEFAULT else {"default": item.default}),
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
