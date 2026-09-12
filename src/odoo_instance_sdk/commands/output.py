"""Output primitives owned by the CLI boundary."""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable
from contextvars import ContextVar
from enum import StrEnum
from functools import wraps
from typing import (
    TYPE_CHECKING,
    Final,
    Generic,
    Never,
    ParamSpec,
    Protocol,
    TypeAliasType,
    TypeVar,
    cast,
    overload,
)

import msgspec
from msgspec import inspect as msgspec_inspect

if TYPE_CHECKING:
    import click
else:
    import rich_click as click
from rich.console import Console
from toon import encode

from odoo_instance_sdk.internal.cli_format import (
    _rich_plan_metadata,
    _rich_semantic_step_lines,
)
from odoo_instance_sdk.internal.database_preparation import DatabasePreparationFailureContext
from odoo_instance_sdk.internal.sanitize import sanitize_last_error, sanitize_terminal_text

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import JsonValue
    from odoo_instance_sdk.internal.proc import StepEvent, StepObserver


def __getattr__(name: str) -> TypeAliasType:
    """Resolve the canonical JSON alias only when a caller explicitly imports it."""
    if name == "JsonValue":
        from odoo_instance_sdk.execution import JsonValue

        globals()[name] = JsonValue
        return JsonValue
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class OutputMode(StrEnum):
    """Output modes understood by the CLI composition layer."""

    RICH = "rich"
    JSON = "json"
    TOON = "toon"


_RICH_COMPLETION_COMMANDS = frozenset(
    {
        "env.checkout",
        "env.sync",
        "test",
        "module.test",
        "module.update",
        "exec",
        "eval",
        "translations.export",
        "db.refresh",
        "db.restore",
        "postgres.up",
        "postgres.approve-image",
    }
)


type JsonObject = dict[str, JsonValue]
type DiagnosticValue = str | BaseException


class OutputError(
    msgspec.Struct,
    frozen=True,
    forbid_unknown_fields=True,
    kw_only=True,
    omit_defaults=True,
):
    """The stable, machine-readable error part of a CLI document."""

    code: str
    message: str
    details: JsonObject | None = msgspec.field(default=None)


class OutputDocument(
    msgspec.Struct,
    frozen=True,
    forbid_unknown_fields=True,
    kw_only=True,
    omit_defaults=True,
):
    """CLI-private immutable v1 document shared by every bounded transport."""

    schema_version: int
    ok: bool
    command: str
    context: JsonValue
    provenance: JsonValue
    dry_run: bool
    warnings: tuple[str, ...]
    # Optional fields are omitted by msgspec when they do not belong to the
    # success/failure variant.  This preserves the existing v1 envelope shape
    # while keeping construction and transport projection typed.
    result: JsonValue = msgspec.field(default=None)
    data: JsonValue = msgspec.field(default=None)
    error: OutputError | None = msgspec.field(default=None)


_ResultT = TypeVar("_ResultT")
_ResultT_co = TypeVar("_ResultT_co", covariant=True)
_P = ParamSpec("_P")

_FIELD_SELECTION: ContextVar[tuple[str, ...] | None] = ContextVar(
    "odcli_field_selection", default=None
)
_FIELD_SCHEMA: ContextVar[type[msgspec.Struct] | None] = ContextVar(
    "odcli_field_schema", default=None
)

_FIELD_SCHEMA_ATTRIBUTE: Final = "__odcli_result_schema__"


def field_schema(
    schema: type[msgspec.Struct],
) -> Callable[[Callable[_P, None]], Callable[_P, None]]:
    """Attach the concrete result schema used by an eligible bounded leaf."""

    def decorate(callback: Callable[_P, None]) -> Callable[_P, None]:
        setattr(callback, _FIELD_SCHEMA_ATTRIBUTE, schema)
        return callback

    return decorate


def _schema_paths(info: msgspec_inspect.Type, prefix: str = "") -> set[str]:
    paths: set[str] = set()
    if isinstance(
        info,
        (msgspec_inspect.StructType, msgspec_inspect.DataclassType, msgspec_inspect.TypedDictType),
    ):
        for field in info.fields:
            path = f"{prefix}.{field.name}" if prefix else field.name
            paths.add(path)
            paths.update(_schema_paths(field.type, path))
    elif isinstance(
        info,
        (
            msgspec_inspect.ListType,
            msgspec_inspect.SetType,
            msgspec_inspect.FrozenSetType,
            msgspec_inspect.VarTupleType,
        ),
    ):
        paths.update(_schema_paths(info.item_type, prefix))
    elif isinstance(info, msgspec_inspect.TupleType):
        for item_type in info.item_types:
            paths.update(_schema_paths(item_type, prefix))
    elif isinstance(info, msgspec_inspect.UnionType):
        for item_type in info.types:
            paths.update(_schema_paths(item_type, prefix))
    return paths


def _field_paths(schema: type[msgspec.Struct]) -> frozenset[str]:
    return frozenset(_schema_paths(msgspec_inspect.type_info(schema)))


def _structural_paths(schema: type[msgspec.Struct] | None) -> frozenset[str]:
    """Read retained structural paths from the concrete result schema."""
    if schema is None:
        return frozenset()
    paths = getattr(schema, "__odcli_structural_paths__", ())
    return frozenset(path for path in paths if isinstance(path, str))


def _schema_for(command: click.Command | Callable[[], None]) -> type[msgspec.Struct] | None:
    schema = getattr(command, _FIELD_SCHEMA_ATTRIBUTE, None)
    if isinstance(schema, type) and issubclass(schema, msgspec.Struct):
        return schema
    return None


def _parse_field_selection(
    raw: str | None,
    *,
    command: str,
    output_format: str | None,
    schema: type[msgspec.Struct] | None,
) -> tuple[str, ...] | None:
    if raw is None:
        return None
    if output_format not in {OutputMode.JSON.value, OutputMode.TOON.value}:
        raise click.UsageError("--fields requires explicit --format json or --format toon")
    if schema is None:
        raise click.UsageError(f"--fields is not supported for {command.replace('_', '.')}")
    fields = tuple(part.strip() for part in raw.split(","))
    if not fields or any(
        not field or any(not piece.isidentifier() for piece in field.split(".")) for field in fields
    ):
        raise click.UsageError("--fields must be a comma-separated list of dotted field names")
    if len(set(fields)) != len(fields):
        raise click.UsageError("--fields must not contain duplicates")
    allowed_paths = _field_paths(schema)
    for field in fields:
        if field not in allowed_paths:
            raise click.UsageError(f"unknown field for {command.replace('_', '.')}: {field}")
    return fields


def _project_selected(value: JsonValue, parts: tuple[str, ...]) -> JsonValue:
    if not parts:
        return value
    if isinstance(value, dict):
        key = parts[0]
        if key not in value:
            return {}
        return {key: _project_selected(value[key], parts[1:])}
    if isinstance(value, list):
        return [_project_selected(item, parts) for item in value]
    return value


def _merge_projection(target: JsonObject, addition: JsonValue) -> None:
    if not isinstance(addition, dict):
        return
    for key, value in addition.items():
        existing = target.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            _merge_projection(existing, value)
        elif isinstance(existing, list) and isinstance(value, list):
            for index, item in enumerate(value):
                if index >= len(existing):
                    existing.append(item)
                else:
                    existing_item = existing[index]
                    if isinstance(existing_item, dict) and isinstance(item, dict):
                        _merge_projection(existing_item, item)
        else:
            target[key] = value


def project_fields(
    result: JsonObject,
    fields: tuple[str, ...] | list[str],
    *,
    schema: type[msgspec.Struct] | None = None,
) -> JsonObject:
    """Project successful result data while retaining structural metadata."""
    selected: JsonObject = {}
    for path in _structural_paths(schema):
        _merge_projection(selected, _project_selected(result, tuple(path.split("."))))
    for field in fields:
        _merge_projection(selected, _project_selected(result, tuple(field.split("."))))
    return selected


def _contains_steps(value: JsonValue) -> bool:
    if isinstance(value, dict):
        return isinstance(value.get("steps"), list) or any(
            _contains_steps(item) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_steps(item) for item in value)
    return False


class _InspectableCommand(Protocol, Generic[_ResultT_co]):
    @property
    def plan(self) -> msgspec.Struct: ...

    def run(
        self, *, observer: StepObserver | None = None, observe_output: bool = False
    ) -> _ResultT_co: ...


@overload
def output_options(command: click.Command) -> click.Command: ...


@overload
def output_options(command: Callable[_P, None]) -> Callable[_P, None]: ...


def output_options(
    command: click.Command | Callable[_P, None],
) -> click.Command | Callable[_P, None]:
    """Add the bounded command's local document-format options."""
    if isinstance(command, click.Command):
        callback = command.callback
        if callback is None:
            return command
        command_name = getattr(callback, "__name__", command.name or "")
        schema = _schema_for(callback)
        click.option(
            "--format",
            "output_format",
            type=click.Choice([mode.value for mode in OutputMode], case_sensitive=True),
            default=None,
            help="Output format (default: rich).",
        )(command)
        if schema is not None:
            click.option(
                "--fields",
                "field_selection",
                default=None,
                help="Comma-separated dotted result fields (JSON/TOON reads only).",
            )(command)

        @wraps(callback)
        def invoke(
            *args: tuple[str, ...],
            **kwargs: str | int | bool | None | tuple[str, ...],
        ) -> None:
            raw_fields = kwargs.pop("field_selection", None)
            output_format = kwargs.get("output_format")
            selected = _parse_field_selection(
                raw_fields if isinstance(raw_fields, str) else None,
                command=command_name,
                output_format=output_format if isinstance(output_format, str) else None,
                schema=schema,
            )
            kwargs.setdefault("json_output", False)
            token = _FIELD_SELECTION.set(selected)
            schema_token = _FIELD_SCHEMA.set(schema)
            try:
                callback(*args, **kwargs)
            finally:
                _FIELD_SCHEMA.reset(schema_token)
                _FIELD_SELECTION.reset(token)

        command.callback = invoke
        return command

    command_name = getattr(command, "__name__", "")
    schema = _schema_for(command)

    @wraps(command)
    def compose(*args: _P.args, **kwargs: _P.kwargs) -> None:
        raw_fields = kwargs.pop("field_selection", None)
        output_format = kwargs.get("output_format")
        selected = _parse_field_selection(
            raw_fields if isinstance(raw_fields, str) else None,
            command=command_name,
            output_format=output_format if isinstance(output_format, str) else None,
            schema=schema,
        )
        # Keep the existing callback signatures source-compatible while the
        # removed option is rejected by Click and never reaches a command.
        kwargs.setdefault("json_output", False)
        token = _FIELD_SELECTION.set(selected)
        schema_token = _FIELD_SCHEMA.set(schema)
        try:
            command(*args, **kwargs)
        finally:
            _FIELD_SCHEMA.reset(schema_token)
            _FIELD_SELECTION.reset(token)

    decorated: click.Command | Callable[_P, None] = click.option(
        "--format",
        "output_format",
        type=click.Choice([mode.value for mode in OutputMode], case_sensitive=True),
        default=None,
        help="Output format (default: rich).",
    )(compose)
    if schema is not None:
        decorated = click.option(
            "--fields",
            "field_selection",
            default=None,
            help="Comma-separated dotted result fields (JSON/TOON reads only).",
        )(decorated)
    return decorated


def command_options(command: Callable[_P, None]) -> Callable[_P, None]:
    """Add format aliases and the local preview switch to a spawning leaf."""
    return click.option(
        "--dry-run", "dry_run", is_flag=True, default=False, help="Inspect without starting."
    )(output_options(command))


def resolve_output_mode(output_format: str | None, json_output: bool = False) -> OutputMode:
    """Resolve the single command-local output format selector."""
    if json_output:
        raise click.UsageError("--json was removed; use --format json")
    if output_format is not None:
        return OutputMode(output_format)
    return OutputMode.JSON if json_output else OutputMode.RICH


def resolve_command_options(
    output_format: str | None,
    json_output: bool,
    dry_run: bool,
    *,
    command: str,
) -> OutputMode:
    """Resolve output aliases and enforce preview-only raw-stream formats."""
    if not dry_run and (output_format is not None or json_output):
        raise click.UsageError(f"--format requires --dry-run for raw-stream {command}")
    return resolve_output_mode(output_format, json_output)


def sanitize_diagnostic(value: DiagnosticValue) -> str:
    """Make every non-interactive diagnostic safe and bounded before emission."""
    return sanitize_last_error(str(value)) or "operation failed"


def model_to_dict(value: msgspec.Struct) -> JsonObject:
    """Project one public typed model into the shared envelope mapping."""
    builtins = msgspec.to_builtins(value)
    if not isinstance(builtins, dict):
        raise TypeError("CLI model result must be an object")
    return cast("JsonObject", builtins)


def _failure_context(error: BaseException | None) -> JsonObject:
    """Project only the typed, secret-free retained-artifact context."""
    context = getattr(error, "failure_context", None) if error is not None else None
    from odoo_instance_sdk.internal.database_replacement import CopyReplacementFailureContext
    from odoo_instance_sdk.internal.pg.drop import DatabaseDropFailureContext

    if not isinstance(
        context,
        (
            DatabasePreparationFailureContext,
            DatabaseDropFailureContext,
            CopyReplacementFailureContext,
        ),
    ):
        return {}
    return model_to_dict(context)


def _failure_message(message: DiagnosticValue, context: JsonObject) -> str:
    rendered = sanitize_diagnostic(message)
    details: list[str] = []
    if context.get("retained_backup_id") is not None:
        details.append(f"retained backup {context['retained_backup_id']}")
    if context.get("retained_database") is not None:
        details.append(f"retained database {context['retained_database']}")
    if context.get("database_confirmed") is not None:
        details.append(f"database confirmed {context['database_confirmed']}")
    if context.get("default_switch_confirmed") is not None:
        details.append(f"default switch confirmed {context['default_switch_confirmed']}")
    sessions = context.get("active_sessions")
    if isinstance(sessions, (list, tuple)) and sessions:
        details.append(
            "active sessions " + json.dumps(sessions, ensure_ascii=False, separators=(",", ":"))
        )
    return f"{rendered}; {'; '.join(details)}" if details else rendered


def rich_print(
    value: str,
    *,
    end: str = "\n",
    preserve_newlines: bool = False,
) -> None:
    """Print human output safely, optionally preserving document line feeds."""
    rendered = sanitize_terminal_text(str(value), preserve_newlines=preserve_newlines)
    Console().print(rendered, markup=False, soft_wrap=True, end=end)


def _sanitize_envelope_value(value: JsonValue, *, preserve_newlines: bool = True) -> JsonValue:
    """Recursively make machine-envelope values inert for terminal transports."""
    if isinstance(value, str):
        # Keep line feeds as data.  JSON/TOON escape them at serialization time,
        # while the Rich plan projection must be able to render captured stdin
        # and scripts as actual multiline blocks.
        return sanitize_terminal_text(value, preserve_newlines=preserve_newlines)
    if isinstance(value, dict):
        return {
            sanitize_terminal_text(key): _sanitize_envelope_value(
                item,
                # User stdout is structured data: retain line feeds so JSON
                # and TOON decoders recover the exact multiline text, while
                # ordinary diagnostic/name fields keep the existing escaped
                # control-character policy.
                preserve_newlines=preserve_newlines or key == "user_stdout",
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _sanitize_envelope_value(item, preserve_newlines=preserve_newlines) for item in value
        ]
    return value


def _document_payload(
    document: OutputDocument,
    *,
    preserve_newlines: bool = True,
    fields: tuple[str, ...] | None = None,
) -> JsonObject:
    """Build the exact v1 envelope projection for one immutable document."""
    payload: JsonObject = {
        "schema_version": document.schema_version,
        "ok": document.ok,
        "command": document.command,
        "context": document.context,
        "provenance": document.provenance,
        "dry_run": document.dry_run,
        "warnings": list(document.warnings),
    }
    if document.ok:
        # ``result`` and ``data`` are intentionally equal in every success
        # document, including an explicit JSON null result.
        result = (
            project_fields(document.result, fields, schema=_FIELD_SCHEMA.get())
            if fields is not None and isinstance(document.result, dict)
            else document.result
        )
        data = (
            project_fields(document.data, fields, schema=_FIELD_SCHEMA.get())
            if fields is not None and isinstance(document.data, dict)
            else document.data
        )
        payload["result"] = result
        payload["data"] = data
    elif document.error is not None:
        error_payload: JsonObject = {
            "code": document.error.code,
            "message": document.error.message,
        }
        if document.error.details is not None:
            error_payload["details"] = document.error.details
        payload["error"] = error_payload
    return cast(
        "JsonObject",
        _sanitize_envelope_value(payload, preserve_newlines=preserve_newlines),
    )


def _document(
    *,
    ok: bool,
    command: str,
    result: JsonObject | None = None,
    context: JsonObject | None = None,
    provenance: JsonObject | None = None,
    dry_run: bool = False,
    warnings: tuple[str, ...] = (),
    error_code: str | None = None,
    error_message: DiagnosticValue | None = None,
    error_details: JsonObject | None = None,
) -> OutputDocument:
    safe_result = _sanitize_envelope_value(result or {}) if ok else None
    message = (
        sanitize_diagnostic(error_message) if error_message is not None else "operation failed"
    )
    error = (
        None
        if ok
        else OutputError(
            code=error_code or command.replace(".", "_") + "_failed",
            message=message,
            details=(
                cast("JsonObject", _sanitize_envelope_value(error_details))
                if error_details is not None
                else None
            ),
        )
    )
    return OutputDocument(
        schema_version=1,
        ok=ok,
        command=command,
        context=_sanitize_envelope_value(context or {}),
        provenance=_sanitize_envelope_value(provenance or {}),
        dry_run=dry_run,
        warnings=warnings,
        result=safe_result,
        data=safe_result,
        error=error,
    )


def _default_rich_projection(document: OutputDocument) -> str:
    """Return a human projection without owning a terminal or output mode."""
    if not document.ok and document.error is not None:
        return document.error.message
    if document.result in (None, {}):
        return ""
    if isinstance(document.result, dict) and _contains_steps(document.result):
        return _rich_plan_projection(document)
    return json.dumps(document.result, ensure_ascii=False, default=str, indent=2)


def emit(
    document: OutputDocument,
    mode: OutputMode,
    *,
    rich: Callable[[OutputDocument], str] | None = None,
    diagnostic: str | None = None,
) -> int:
    """Emit one immutable document and return its normal CLI exit status."""
    payload = _document_payload(
        document,
        preserve_newlines=mode is OutputMode.RICH,
        fields=_FIELD_SELECTION.get() if mode is not OutputMode.RICH else None,
    )
    if mode is OutputMode.JSON:
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    elif mode is OutputMode.TOON:
        click.echo(encode(payload))
    elif document.ok:
        rendered = _rich_rendered(document, rich)
        if rendered:
            rich_print(rendered, preserve_newlines=True)
    else:
        rendered = _rich_rendered(document, rich)
        click.echo(sanitize_diagnostic(rendered), err=True)
    if diagnostic:
        click.echo(sanitize_diagnostic(diagnostic), err=True)
    return 0 if document.ok else 1


def _rich_rendered(
    document: OutputDocument,
    projection: Callable[[OutputDocument], str] | None,
) -> str:
    """Add the one common completion line to a successful Rich document."""
    # Every dry-run process plan uses the same semantic projection.  Command
    # adapters may keep their execution-result renderer for normal runs, but
    # must not replace captured process displays with a result-shaped summary
    # while previewing the immutable plan.
    if (
        document.ok
        and document.dry_run
        and isinstance(document.result, dict)
        and _contains_steps(document.result)
    ):
        rendered = _rich_plan_projection(document)
        if (
            isinstance(document.provenance, dict) and "ticket_allocation" in document.provenance
        ) or document.command == "test":
            annotation = projection(document) if projection is not None else ""
            if annotation:
                rendered = f"{rendered}\n{annotation}" if rendered else annotation
    else:
        rendered = (projection or _default_rich_projection)(document)
    if not document.ok or document.dry_run or document.command not in _RICH_COMPLETION_COMMANDS:
        return rendered
    completion = _rich_success_completion(document)
    if not completion:
        return rendered
    return f"{rendered}\n{completion}" if rendered else completion


def _rich_success_completion(document: OutputDocument) -> str:
    """Project only applicable public summary fields for a Rich success."""
    if not document.ok or document.dry_run:
        return ""
    result = document.result
    if not isinstance(result, dict):
        result = {}

    aliases = {
        "database": ("database", "restored_database", "target_database"),
        "url": ("url", "http_url"),
        "backup": ("backup", "backup_id", "backup_uuid"),
        "modules": ("modules",),
    }
    fields = ["status=success"]
    for label, candidates in aliases.items():
        value = next(
            (result[candidate] for candidate in candidates if candidate in result),
            None,
        )
        if value in (None, "", (), [], {}):
            continue
        if isinstance(value, (dict, list, tuple)):
            rendered_value = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
        else:
            rendered_value = str(value)
        fields.append(f"{label}={rendered_value}")

    if document.command == "exec":
        transaction = result.get("transaction")
        if transaction is None:
            commit = result.get("commit")
            transaction = "commit" if commit is True else "rollback" if commit is False else None
        if transaction in {"commit", "rollback"}:
            fields.append(f"transaction={transaction}")
    return sanitize_terminal_text(" ".join(fields), preserve_newlines=True)


def run_rich_bounded(  # noqa: C901
    run: Callable[[StepObserver], _ResultT],
    *,
    show_command_output: bool = False,
    console: Console | None = None,
    include_elapsed: bool = True,
) -> _ResultT:
    """Run one bounded command with the single shared lifecycle observer.

    Redirected Rich output is deliberately sparse and deterministic.  A TTY
    gets the same lines through one transient ``Live`` view, so renderer
    cleanup cannot alter the command's result or exception semantics.
    """
    console = console or Console()
    lines: list[str] = []
    started: dict[str, float] = {}
    update: Callable[[str], None] | None = None

    def render(event: StepEvent) -> None:  # noqa: C901
        now = time.monotonic()
        if event.kind in {"stdout", "stderr"} and not show_command_output:
            return
        if event.kind == "started":
            started[event.step_id] = now
        elapsed = event.elapsed
        if elapsed is None and event.step_id in started:
            elapsed = max(0.0, now - started[event.step_id])
        parts = [f"[{event.step_id}] {event.kind}"]
        if event.operation and event.operation != event.step_id:
            parts.append(f"operation={event.operation}")
        if event.target and event.target != event.step_id:
            parts.append(f"target={event.target}")
        if event.chunk and event.kind in {"stdout", "stderr"}:
            parts[-1] += f": {event.chunk}"
        if event.completed_units is not None:
            units = str(event.completed_units)
            if event.total_units is not None:
                units += f"/{event.total_units}"
                if event.total_units > 0:
                    units += f" ({event.completed_units / event.total_units:.0%})"
            parts.append(f"units={units}")
        if event.returncode is not None and event.kind not in {"stdout", "stderr"}:
            parts.append(f"(exit {event.returncode})")
        if (
            include_elapsed
            and elapsed is not None
            and event.kind in {"started", "progress", "completed", "failed"}
        ):
            parts.append(f"elapsed={elapsed:.3f}s")
        if event.error:
            parts.append(f"error={event.error}")
        line = sanitize_terminal_text(" ".join(parts), preserve_newlines=True)
        rendered = line.splitlines() or [line]
        lines.extend(rendered)
        if update is not None:
            update("\n".join(lines))
        else:
            for item in rendered:
                rich_print(item)

    observer = render
    if console.is_terminal:
        from rich.live import Live

        with Live("", console=console, transient=True) as live:

            def update_live(value: str) -> None:
                live.update(value, refresh=True)

            update = update_live
            return run(observer)
    return run(observer)


def success_document(
    *,
    command: str,
    result: JsonObject | None = None,
    context: JsonObject | None = None,
    provenance: JsonObject | None = None,
    dry_run: bool = False,
    warnings: tuple[str, ...] = (),
) -> OutputDocument:
    """Construct a typed successful v1 document for the shared emitter."""
    return _document(
        ok=True,
        command=command,
        result=result,
        context=context,
        provenance=provenance,
        dry_run=dry_run,
        warnings=warnings,
    )


def failure_document(
    *,
    command: str,
    context: JsonObject | None = None,
    provenance: JsonObject | None = None,
    dry_run: bool,
    warnings: tuple[str, ...] = (),
    error_code: str | None = None,
    error_message: DiagnosticValue | None = None,
    error_details: JsonObject | None = None,
) -> OutputDocument:
    """Construct a typed failed v1 document for the shared emitter."""
    return _document(
        ok=False,
        command=command,
        context=context,
        provenance=provenance,
        dry_run=dry_run,
        warnings=warnings,
        error_code=error_code,
        error_message=error_message,
        error_details=error_details,
    )


def action_command(
    step_id: str,
    operation: Callable[[], _ResultT],
    *,
    description: str | None = None,
    mutating: bool = False,
) -> _InspectableCommand[_ResultT]:
    """Capture a bounded domain operation behind one command-local action.

    Process-backed SDK operations provide richer command siblings.  This
    adapter is for bounded leaves whose domain operation is already the
    canonical process boundary or a read-only computation; it still gives
    preview and execution one immutable command object and one ledger entry.
    """
    from odoo_instance_sdk.execution import ActionStep, Command, ExecutionPlan
    from odoo_instance_sdk.internal.proc import PreparedAction, RunContext

    action = PreparedAction(
        step_id=step_id,
        action=step_id,
        description=description or step_id,
        mutating=mutating,
    )

    def callback(context: RunContext[_ResultT]) -> _ResultT:
        context.action(step_id)
        result = operation()
        context.complete_action(step_id)
        return result

    return Command.create(
        ExecutionPlan(
            steps=(
                ActionStep(
                    step_id=step_id,
                    action=step_id,
                    description=description or step_id,
                    mutating=mutating,
                ),
            )
        ),
        callback,
        (action,),
    )


def run_or_preview(  # noqa: C901
    build_command: Callable[[], _InspectableCommand[_ResultT]],
    *,
    command_name: str,
    mode: OutputMode,
    dry_run: bool,
    result: Callable[[_ResultT | None], JsonObject] | None = None,
    context: JsonObject | None = None,
    provenance: JsonObject | None = None,
    confirm: Callable[[], None] | None = None,
    rich: Callable[[OutputDocument], str] | None = None,
    preview: Callable[[_InspectableCommand[_ResultT]], JsonObject] | None = None,
    emit_normal: bool = True,
    observer: StepObserver | None = None,
    observe_output: bool = False,
    progress: bool = False,
    on_interrupt: Callable[[KeyboardInterrupt], None] | None = None,
) -> tuple[int, _ResultT | None]:
    """Build one command, then either inspect it or run that same instance.

    Confirmation is deliberately invoked after construction, so a caller can
    present or validate the complete snapshot before asking for consent.
    """
    command = build_command()
    if dry_run:
        return (
            emit(
                success_document(
                    command=command_name,
                    result=preview(command) if preview is not None else model_to_dict(command.plan),
                    context=context,
                    provenance=provenance,
                    dry_run=True,
                ),
                mode,
                rich=rich,
            ),
            None,
        )
    if confirm is not None:
        confirm()

    def execute(active_observer: StepObserver | None) -> _ResultT:
        if active_observer is None:
            return command.run()
        return command.run(observer=active_observer, observe_output=observe_output)

    if progress and observer is None and mode is OutputMode.RICH:
        try:
            value = run_rich_bounded(
                execute,
                show_command_output=observe_output,
            )
        except KeyboardInterrupt as exc:
            if on_interrupt is not None:
                on_interrupt(exc)
            raise click.exceptions.Exit(130) from exc
    else:
        try:
            value = execute(observer)
        except KeyboardInterrupt as exc:
            if on_interrupt is not None:
                on_interrupt(exc)
                raise click.exceptions.Exit(130) from exc
            if progress:
                raise click.exceptions.Exit(130) from exc
            raise
    if not emit_normal:
        return 0, value
    payload = result(value) if result is not None else {}
    status = emit(
        success_document(
            command=command_name,
            result=payload,
            context=context,
            provenance=provenance,
        ),
        mode,
        rich=rich,
    )
    return status, value


def build_envelope(
    *,
    ok: bool,
    command: str,
    result: JsonObject | None = None,
    context: JsonObject | None = None,
    provenance: JsonObject | None = None,
    dry_run: bool = False,
    error_code: str | None = None,
    error_message: DiagnosticValue | None = None,
    error_details: JsonObject | None = None,
) -> JsonObject:
    """Build the existing v1 envelope without selecting an output transport."""
    document = (
        success_document(
            command=command,
            result=result,
            context=context,
            provenance=provenance,
            dry_run=dry_run,
        )
        if ok
        else failure_document(
            command=command,
            context=context,
            provenance=provenance,
            dry_run=dry_run,
            error_code=error_code,
            error_message=error_message,
            error_details=error_details,
        )
    )
    return _document_payload(
        document,
        preserve_newlines=False,
    )


def emit_json_envelope(
    *,
    ok: bool,
    command: str,
    result: JsonObject | None = None,
    context: JsonObject | None = None,
    provenance: JsonObject | None = None,
    dry_run: bool = False,
    error_code: str | None = None,
    error_message: DiagnosticValue | None = None,
    error_details: JsonObject | None = None,
    mode: OutputMode = OutputMode.JSON,
) -> None:
    """Emit the v1 envelope as exactly one JSON or TOON stdout document."""
    emit(
        success_document(
            command=command,
            result=result,
            context=context,
            provenance=provenance,
            dry_run=dry_run,
        )
        if ok
        else failure_document(
            command=command,
            context=context,
            provenance=provenance,
            dry_run=dry_run,
            error_code=error_code,
            error_message=error_message,
            error_details=error_details,
        ),
        mode,
    )


def fail(
    output_mode: OutputMode,
    command: str,
    message: DiagnosticValue,
    *,
    dry_run: bool,
    usage: bool = False,
    error_code: str | None = None,
    details: JsonObject | None = None,
) -> Never:
    mode = output_mode
    context = _failure_context(message if isinstance(message, BaseException) else None)
    from odoo_instance_sdk.internal.pg.drop import DatabaseDropSafetyError

    if isinstance(message, DatabaseDropSafetyError) and details is None:
        details = context
    rendered_message = _failure_message(message, context)
    if mode is not OutputMode.RICH:
        emit(
            failure_document(
                command=command,
                context=context,
                dry_run=dry_run,
                error_code=error_code
                or ("usage_error" if usage else command.replace(".", "_") + "_failed"),
                error_message=rendered_message,
                error_details=details,
            ),
            mode,
        )
    else:
        emit(
            failure_document(
                command=command,
                dry_run=dry_run,
                error_message=rendered_message,
                error_details=details,
            ),
            mode,
        )
    if usage:
        raise click.exceptions.Exit(2)
    sys.exit(1)


__all__ = [
    "JsonValue",
    "OutputDocument",
    "OutputError",
    "OutputMode",
    "action_command",
    "build_envelope",
    "command_options",
    "emit",
    "emit_json_envelope",
    "fail",
    "failure_document",
    "field_schema",
    "model_to_dict",
    "output_options",
    "project_fields",
    "resolve_command_options",
    "resolve_output_mode",
    "rich_print",
    "run_or_preview",
    "run_rich_bounded",
    "sanitize_diagnostic",
    "sanitize_terminal_text",
    "success_document",
]


def _rich_plan_projection(document: OutputDocument) -> str:
    """Render one captured plan as readable, fully redacted human text.

    This is intentionally a pure projection.  It receives the same immutable
    document as JSON and TOON, and therefore cannot launch a process, prompt,
    or rebuild any command input.
    """
    result = document.result
    if not isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False, default=str, indent=2)

    lines = [f"Plan: {document.command}"]
    plan_values = tuple(_nested_plans(result))
    semantic = _semantic_plan_projection(
        result,
        command=document.command,
        document_warnings=document.warnings,
    )
    if semantic is not None and len(plan_values) == 1 and plan_values[0] is result:
        return semantic
    number = 0
    for plan in plan_values:
        steps = plan.get("steps")
        if isinstance(steps, list):
            for item in steps:
                if isinstance(item, dict):
                    number += 1
                    lines.extend(_rich_step_lines(number, item))
        lines.extend(_rich_plan_metadata(plan, document.warnings if plan is result else ()))
    return "\n".join(lines)


def _nested_plans(value: JsonValue) -> list[dict[str, JsonValue]]:
    """Find captured plans without changing their document traversal order."""
    plans: list[dict[str, JsonValue]] = []
    if isinstance(value, dict):
        if isinstance(value.get("steps"), list):
            plans.append(value)
        else:
            for child in value.values():
                plans.extend(_nested_plans(child))
    elif isinstance(value, list):
        for child in value:
            plans.extend(_nested_plans(child))
    return plans


def _semantic_plan_projection(
    result: dict[str, JsonValue],
    *,
    command: str,
    document_warnings: tuple[str, ...] = (),
) -> str | None:
    """Render the single decision-oriented semantic plan observation."""
    observations = result.get("observations")
    if not isinstance(observations, list):
        return None
    semantic = next(
        (
            item
            for item in observations
            if isinstance(item, dict) and item.get("kind") == "semantic"
        ),
        None,
    )
    if not isinstance(semantic, dict):
        return None
    lines = [f"Plan: {command}", f"Goal: {semantic.get('goal', '')}"]
    for field, label in (("targets", "Targets"), ("mutations", "Mutations")):
        values = semantic.get(field)
        if isinstance(values, list) and values:
            lines.append(f"{label}:")
            lines.extend(f"  - {value}" for value in values)
    preconditions = semantic.get("preconditions")
    if isinstance(preconditions, list) and preconditions:
        lines.append("Preconditions:")
        for item in preconditions:
            if isinstance(item, dict):
                lines.append(
                    f"  - {item.get('name', 'precondition')}: "
                    f"{item.get('status', 'unknown')} — {item.get('detail', '')}"
                )
    sessions = semantic.get("active_sessions")
    if isinstance(sessions, list) and sessions:
        lines.extend(_rich_active_session_lines(sessions))
    warnings = semantic.get("warnings")
    warning_values = list(warnings) if isinstance(warnings, list) else []
    warning_values.extend(warning for warning in document_warnings if warning not in warning_values)
    if warning_values:
        lines.append("Warnings:")
        lines.extend(f"  - {warning}" for warning in warning_values)
    lines.extend(_rich_semantic_step_lines(result))
    return "\n".join(lines)


def _rich_active_session_lines(sessions: list[JsonValue]) -> list[str]:
    lines = ["Active sessions:"]
    for session in sessions:
        if isinstance(session, dict):
            identity = ", ".join(
                f"{key}={session[key]}"
                for key in ("pid", "user", "client", "application")
                if session.get(key) is not None
            )
            lines.append(f"  - {identity}")
    return lines


def _rich_step_lines(number: int, item: dict[str, JsonValue]) -> list[str]:
    kind = str(item.get("kind", "step"))
    step_id = str(item.get("step_id", "<unnamed>"))
    flags = tuple(
        name
        for name, enabled in (
            ("mutating", item.get("mutating")),
            ("interactive", item.get("interactive")),
            ("long-running", item.get("long_running")),
            ("read-only", item.get("read_only")),
        )
        if enabled is True
    )
    classification = ", ".join(flags) or "bounded"
    lines = [f"{number}. {kind} {step_id} [{classification}]"]
    lines.append(f"   classification: {classification}")
    if kind == "process":
        return lines + _rich_process_lines(item)
    if "description" in item:
        lines.append(f"   action: {item.get('description')}")
    return lines


def _rich_process_lines(item: dict[str, JsonValue]) -> list[str]:
    lines: list[str] = []
    display = item.get("display")
    if isinstance(display, str) and display:
        lines.append(f"   command: {display}")
    argv = item.get("argv")
    if isinstance(argv, list):
        lines.append("   argv: " + json.dumps(argv, ensure_ascii=False, separators=(", ", ": ")))
    for field, label in (
        ("executable", "executable"),
        ("cwd", "cwd"),
        ("mode", "mode"),
        ("timeout", "timeout"),
    ):
        value = item.get(field)
        if value is not None:
            lines.append(f"   {label}: {value}")
    environment = item.get("environment_overrides")
    if isinstance(environment, list) and environment:
        lines.append(
            "   environment: "
            + json.dumps(environment, ensure_ascii=False, separators=(", ", ": "))
        )
    stdin = item.get("input_preview")
    if isinstance(stdin, str):
        lines.append("   stdin: |")
        lines.extend(f"     {line}" for line in (stdin.splitlines() or [""]))
    return lines
