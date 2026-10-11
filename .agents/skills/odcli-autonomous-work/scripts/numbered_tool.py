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
from typing import Any

from fix_tool import _canonical_interpreter, _canonical_source, _run

_TRUSTED_ARG = "--_trusted-canonical"


def _load_environment_options(path: Path) -> Any:
    """Decode only caller-supplied environment inputs; never echo them."""
    from odoo_instance_sdk.models import EnvironmentDatabaseMode
    from odoo_instance_sdk.resources.environment import EnvironmentCheckoutOptions

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("environment options must be a JSON object")
    for name in ("config_path", "odoo_bin", "hash_lock"):
        if name in payload and payload[name] is not None:
            payload[name] = Path(payload[name])
    if "db_mode" in payload:
        payload["db_mode"] = EnvironmentDatabaseMode(payload["db_mode"])
    return EnvironmentCheckoutOptions(**payload)


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
