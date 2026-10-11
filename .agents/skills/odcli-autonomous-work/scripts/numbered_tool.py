#!/usr/bin/env python3
"""Private service entry point for numbered OdCLI installations.

The public ``odcli`` command never imports this module.  The wrapper first
restarts itself with the trusted canonical interpreter, verifies its installed
repository provenance, and only then calls the package-owned coordinator.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

from fix_tool import _canonical_interpreter, _canonical_source, _run

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import JsonValue
    from odoo_instance_sdk.resources.environment import EnvironmentCheckoutOptions

_TRUSTED_ARG = "--_trusted-canonical"


def _optional_string(payload: dict[str, JsonValue], name: str) -> str | None:
    value = payload.get(name)
    if value is not None and not isinstance(value, str):
        raise TypeError(f"{name} must be a string or null")
    return value


def _optional_path(payload: dict[str, JsonValue], name: str) -> Path | None:
    value = _optional_string(payload, name)
    return None if value is None else Path(value)


def _optional_bool(payload: dict[str, JsonValue], name: str) -> bool | None:
    value = payload.get(name)
    if value is not None and not isinstance(value, bool):
        raise TypeError(f"{name} must be a boolean or null")
    return value


def _optional_int(payload: dict[str, JsonValue], name: str) -> int | None:
    value = payload.get(name)
    if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
        raise TypeError(f"{name} must be an integer or null")
    return value


def _json_object(value: JsonValue) -> dict[str, JsonValue]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise TypeError("environment options must be a JSON object")
    return value


def _load_environment_options(path: Path) -> EnvironmentCheckoutOptions:
    """Decode only caller-supplied environment inputs; never echo them."""
    from odoo_instance_sdk.models import EnvironmentDatabaseMode
    from odoo_instance_sdk.resources.environment import EnvironmentCheckoutOptions

    payload = _json_object(cast("JsonValue", json.loads(path.read_text(encoding="utf-8"))))
    accepted = {
        "base_ref",
        "name",
        "config_path",
        "db_mode",
        "source_database",
        "target_database",
        "odoo_bin",
        "python",
        "create_venv",
        "http_port",
        "hash_lock",
        "hash_lock_sha256",
        "remote_name",
        "backup_id",
    }
    unknown = set(payload) - accepted
    if unknown:
        raise TypeError(f"unsupported environment option: {sorted(unknown)[0]}")
    db_mode_value = _optional_string(payload, "db_mode")
    db_mode = (
        EnvironmentDatabaseMode.SHARED
        if db_mode_value is None
        else EnvironmentDatabaseMode(db_mode_value)
    )
    return EnvironmentCheckoutOptions(
        base_ref=_optional_string(payload, "base_ref"),
        name=_optional_string(payload, "name"),
        config_path=_optional_path(payload, "config_path"),
        db_mode=db_mode,
        source_database=_optional_string(payload, "source_database"),
        target_database=_optional_string(payload, "target_database"),
        odoo_bin=_optional_path(payload, "odoo_bin"),
        python=_optional_string(payload, "python"),
        create_venv=_optional_bool(payload, "create_venv") or False,
        http_port=_optional_int(payload, "http_port"),
        hash_lock=_optional_path(payload, "hash_lock"),
        hash_lock_sha256=_optional_string(payload, "hash_lock_sha256"),
        remote_name=_optional_string(payload, "remote_name"),
        backup_id=_optional_string(payload, "backup_id"),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="allocate and create one numbered identity")
    create.add_argument("--project", type=Path, required=True)
    create.add_argument("--reference-checkout", type=Path, required=True)
    create.add_argument("--checkout-parent", type=Path, required=True)
    create.add_argument("--reference-sha", required=True)
    create.add_argument("--sdk-sha", required=True)
    create.add_argument("--environment-json", type=Path, required=True)
    create.add_argument("--odoo-executable", required=True)
    create.add_argument("--source-repository")

    listing = commands.add_parser("list", help="list bounded numbered identity evidence")
    listing.add_argument("--limit", type=int, default=100)

    inspect = commands.add_parser("inspect", help="inspect one numbered identity")
    inspect.add_argument("identifier")

    remove = commands.add_parser("remove", help="remove one eligible numbered identity")
    remove.add_argument("identifier")
    remove.add_argument("--checkout-parent", type=Path, required=True)
    remove.add_argument("--odoo-executable", required=True)
    return parser


def _dispatch(argv: list[str]) -> int:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.config import OdooClientConfig
    from odoo_instance_sdk.internal.agent_checkout import (
        NumberedCheckoutOptions,
        create_numbered_installation,
        inspect_numbered_installation,
        list_numbered_installations,
        remove_numbered_installation,
    )
    from odoo_instance_sdk.project import ProjectConfig

    args = _parser().parse_args(argv)
    if args.command == "list":
        print(json.dumps(list_numbered_installations(limit=args.limit), sort_keys=True))
        return 0
    if args.command == "inspect":
        print(json.dumps(inspect_numbered_installation(args.identifier), sort_keys=True))
        return 0

    client = OdooClient(config=OdooClientConfig(executable=args.odoo_executable))
    if args.command == "remove":
        remove_numbered_installation(
            args.identifier,
            client=client,
            checkout_parent=args.checkout_parent,
        )
        print(json.dumps({"kind": "numbered", "identifier": args.identifier, "removed": True}))
        return 0

    options = NumberedCheckoutOptions(
        client=client,
        project=ProjectConfig.load(args.project),
        reference_checkout=args.reference_checkout,
        checkout_parent=args.checkout_parent,
        reference_sha=args.reference_sha,
        sdk_sha=args.sdk_sha,
        environment_options=_load_environment_options(args.environment_json),
        source_repository=args.source_repository,
    )
    print(json.dumps(create_numbered_installation(options).to_json(), sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        canonical = _canonical_interpreter()
        _canonical_source(canonical)
        if (
            not arguments
            or arguments[0] != _TRUSTED_ARG
            or Path(sys.executable).resolve() != canonical.resolve()
        ):
            result = _run(
                [str(canonical), str(Path(__file__).resolve()), _TRUSTED_ARG, *arguments],
                env={**os.environ, "ODCLI_NUMBERED_SERVICE": "1"},
            )
            sys.stdout.write(result)
            return 0
        return _dispatch(arguments[1:])
    except (OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"numbered service failed: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
