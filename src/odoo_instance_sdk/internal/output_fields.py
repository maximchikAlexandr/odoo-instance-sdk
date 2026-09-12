"""Typed field selection owned by the CLI output boundary."""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from functools import wraps
from typing import TYPE_CHECKING, ParamSpec, Protocol, cast, overload

import msgspec
from msgspec import inspect as msgspec_inspect

if TYPE_CHECKING:
    import click

    from odoo_instance_sdk.execution import JsonValue
else:
    import rich_click as click


type JsonObject = dict[str, JsonValue]
type CommandArgument = str | bool | int | float | None
_P = ParamSpec("_P")
_FIELD_SCHEMA_ATTRIBUTE = "__odcli_result_schema__"
_FIELD_SELECTION: ContextVar[tuple[str, ...] | None] = ContextVar(
    "odcli_field_selection", default=None
)
_FIELD_SCHEMA: ContextVar[type[msgspec.Struct] | None] = ContextVar(
    "odcli_field_schema", default=None
)


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
    """Read structural fields from annotations on the concrete result schema."""
    if schema is None:
        return frozenset()
    from typing import Annotated, get_args, get_origin, get_type_hints

    try:
        annotations = get_type_hints(schema, include_extras=True)
    except (NameError, TypeError):
        return frozenset()
    return frozenset(
        name
        for name, annotation in annotations.items()
        if get_origin(annotation) is Annotated and "odcli-structural" in get_args(annotation)[1:]
    )


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
    if output_format not in {"json", "toon"}:
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
    """Project result fields while retaining schema-annotated metadata."""
    selected: JsonObject = {}
    for path in _structural_paths(schema):
        _merge_projection(selected, _project_selected(result, (path,)))
    for field in fields:
        _merge_projection(selected, _project_selected(result, tuple(field.split("."))))
    return selected


def current_field_selection() -> tuple[str, ...] | None:
    return _FIELD_SELECTION.get()


def current_field_schema() -> type[msgspec.Struct] | None:
    return _FIELD_SCHEMA.get()


class _CommandCallback(Protocol):
    def __call__(self, *args: CommandArgument, **kwargs: CommandArgument) -> None: ...


def _invoke(
    callback: _CommandCallback,
    args: tuple[CommandArgument, ...],
    kwargs: dict[str, CommandArgument],
    *,
    command_name: str,
    schema: type[msgspec.Struct] | None,
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


@overload
def output_options(command: click.Command) -> click.Command: ...


@overload
def output_options(command: Callable[_P, None]) -> Callable[_P, None]: ...


def output_options(
    command: click.Command | Callable[_P, None],
) -> click.Command | Callable[_P, None]:
    """Add format and typed field options with one scoped callback lifecycle."""
    if isinstance(command, click.Command):
        callback = command.callback
        if callback is None:
            return command
        command_name = getattr(callback, "__name__", command.name or "")
        schema = _schema_for(callback)
        click.option(
            "--format",
            "output_format",
            type=click.Choice(["rich", "json", "toon"], case_sensitive=True),
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
        def invoke(*args: CommandArgument, **kwargs: CommandArgument) -> None:
            _invoke(callback, args, kwargs, command_name=command_name, schema=schema)

        command.callback = invoke
        return command

    command_name = getattr(command, "__name__", "")
    schema = _schema_for(command)

    @wraps(command)
    def compose(*args: _P.args, **kwargs: _P.kwargs) -> None:
        _invoke(
            cast("_CommandCallback", command),
            cast("tuple[CommandArgument, ...]", args),
            cast("dict[str, CommandArgument]", kwargs),
            command_name=command_name,
            schema=schema,
        )

    decorated: click.Command | Callable[_P, None] = click.option(
        "--format",
        "output_format",
        type=click.Choice(["rich", "json", "toon"], case_sensitive=True),
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
