"""Stable metadata contracts shared by the local machine-operation boundary.

This module deliberately contains metadata and DTO projection only. Importing
it does not resolve a project, open a catalogue, or import an operation domain.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, cast

import msgspec

from odoo_instance_sdk.execution import Command, JsonValue

if TYPE_CHECKING:
    from odoo_instance_sdk.commands.context import OperationContext

from .inventory import build_public_leaf_cases
from .serialization import (
    assert_compatible_bundle,
    assert_compatible_contract,
    contract_bundle,
    contract_bytes,
    wire_projection,
    wire_schema,
)

CONTRACT_VERSION = 1
ENTRY_POINT_GROUP = "odoo_instance_sdk.operations"


class OperationTransport(StrEnum):
    """The one transport an operation binding owns."""

    DOCUMENT = "finite-document"
    SESSION = "bounded-jsonl-session"
    NATIVE_TTY = "native-tty"
    JSONL_STREAM = "jsonl-stream"
    INTERACTIVE = "interactive"


@dataclass(frozen=True, slots=True)
class _NoDefault:
    """Sentinel for a parameter without a wire default."""


_NO_DEFAULT = _NoDefault()
type ParameterDefault = JsonValue | tuple[str, ...] | _NoDefault
type FactoryResult = JsonValue | msgspec.Struct | Command[JsonValue]


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
    default: ParameterDefault = _NO_DEFAULT


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


type OperationFactory = Callable[[msgspec.Struct | None, OperationContext], FactoryResult]


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


def _builtin_factory(case: PublicLeafCase) -> OperationFactory | None:
    """Create a lazy factory backed by the finite typed adapter table."""

    reference = case.sdk_primitive
    if reference is None and case.path != ("contract", "export"):
        return None

    def factory(request: msgspec.Struct | None, context: OperationContext) -> FactoryResult:
        from odoo_instance_sdk.commands.context import OperationContext

        if not isinstance(context, OperationContext):
            raise TypeError("operation factory requires OperationContext")
        raw_values = msgspec.to_builtins(request) if isinstance(request, msgspec.Struct) else {}
        if not isinstance(raw_values, dict):
            raise TypeError("operation request must project to an object")
        values = cast("dict[str, JsonValue]", raw_values)
        if case.path == ("contract", "export"):
            import hashlib

            payload = contract_bytes()
            return {
                "contract_version": CONTRACT_VERSION,
                "entry_point_group": ENTRY_POINT_GROUP,
                "operation_count": len(PUBLIC_LEAF_CASES),
                "bundle_sha256": hashlib.sha256(payload).hexdigest(),
            }
        if reference is None:
            raise RuntimeError(f"operation {case.path!r} is not available for this context")
        # The adapter table keeps eval_expression_command, exec_script_command,
        # and run_odoo_tests_command imports lazy while making every callable
        # reference explicit.
        from .adapters import BUILTIN_ADAPTERS

        adapter = BUILTIN_ADAPTERS.get(reference)
        if adapter is None:
            raise RuntimeError(f"operation {case.path!r} is not available for this context")
        return adapter(case, values, context)

    return factory


def _validate_builtin_adapter_inventory() -> None:
    from .adapters import BUILTIN_ADAPTERS

    references = {
        case.sdk_primitive for case in PUBLIC_LEAF_CASES if case.sdk_primitive is not None
    }
    if references != set(BUILTIN_ADAPTERS):
        raise RuntimeError(
            "built-in adapter inventory mismatch: "
            f"missing={sorted(references - set(BUILTIN_ADAPTERS))!r}, "
            f"extra={sorted(set(BUILTIN_ADAPTERS) - references)!r}"
        )


def builtin_bindings() -> tuple[OperationBinding, ...]:
    """Build the data-only built-in bindings in canonical inventory order."""
    _validate_builtin_adapter_inventory()
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

    def validate_click_tree(self, command: ClickCommand) -> None:
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

    def bundle(self) -> dict[str, JsonValue]:
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


class ClickCommand(Protocol):
    """Small structural view of the composed Click tree."""

    commands: Mapping[str, ClickCommand]
    callback: ClickCallback | None


class ClickCallback(Protocol):
    def __call__(self, *args: JsonValue, **kwargs: JsonValue) -> JsonValue: ...


class ProviderCallable(Protocol):
    contract_version: int

    def __call__(self) -> tuple[OperationBinding, ...]: ...


class ProviderBindings(Protocol):
    contract_version: int

    def bindings(self) -> tuple[OperationBinding, ...]: ...


class ProviderProvide(Protocol):
    contract_version: int

    def provide(self) -> tuple[OperationBinding, ...]: ...


type ProviderSource = ProviderCallable | ProviderBindings | ProviderProvide


def click_leaf_paths(
    command: ClickCommand, prefix: tuple[str, ...] = ()
) -> tuple[tuple[str, ...], ...]:
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
    command: ClickCommand, prefix: tuple[str, ...] = ()
) -> tuple[tuple[tuple[str, ...], ClickCommand], ...]:
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
    providers: Iterable[ProviderSource] = (),
) -> OperationRegistry:
    """Return the validated core registry without importing provider domains."""
    rows = list(builtin_bindings() if bindings is None else tuple(bindings))
    for provider in providers:
        rows.extend(provider_bindings(provider))
    return OperationRegistry(rows)


def provider_bindings(provider: ProviderSource) -> tuple[OperationBinding, ...]:
    """Read and strictly validate one installed provider's finite bindings."""
    version = getattr(provider, "contract_version", CONTRACT_VERSION)
    if version != CONTRACT_VERSION:
        raise ValueError(f"unsupported provider contract version: {version}")
    if callable(provider):
        rows = provider()
    elif callable(getattr(provider, "bindings", None)):
        rows = cast("ProviderBindings", provider).bindings()
    elif callable(getattr(provider, "provide", None)):
        rows = cast("ProviderProvide", provider).provide()
    else:
        raise TypeError("operation provider has no bindings callable")
    if not isinstance(rows, tuple):
        raise TypeError("operation provider must return a finite tuple")
    for row in rows:
        if not isinstance(row, OperationBinding) or row.factory is None:
            raise ValueError("operation provider returned an incomplete binding")
        try:
            inspect.signature(row.factory).bind(None, None)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "operation provider factory must accept request and context"
            ) from error
        if not row.descriptor.provider or row.descriptor.provider == "core":
            raise ValueError("provider bindings must identify their provider")
    _validate_bindings(rows)
    return rows


__all__ = [
    "CONTRACT_VERSION",
    "ENTRY_POINT_GROUP",
    "PUBLIC_LEAF_CASES",
    "ClickCommand",
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
