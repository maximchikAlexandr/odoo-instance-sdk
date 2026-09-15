#!/usr/bin/env python3
"""One-shot split of models.py into the models/ package (block 5.3)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS_PY = ROOT / "src/odoo_instance_sdk/models.py"
MODELS_DIR = ROOT / "src/odoo_instance_sdk/models"

VALIDATORS_HEADER = """from __future__ import annotations

import math
from datetime import datetime
from typing import TypeVar

_TupleItem = TypeVar("_TupleItem")

"""

LITERALS_HEADER = """from __future__ import annotations

from typing import Literal, TypeVar

_TupleItem = TypeVar("_TupleItem")

type ModuleJsonValue = (
    None | bool | int | float | str | list["ModuleJsonValue"] | dict[str, "ModuleJsonValue"]
)

"""

COMMON = """from __future__ import annotations

import enum
import math
import uuid
import warnings
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal, TypeVar, cast

import msgspec

from odoo_instance_sdk.models._validators import (
    _require_bool,
    _require_datetime,
    _require_non_negative_float,
    _require_non_negative_int,
    _require_ratio,
    _require_tuple,
)

"""

SPLITS: list[tuple[str, int, int, str]] = [
    ("_validators.py", 41, 78, VALIDATORS_HEADER),
    ("_literals.py", 13, 38, LITERALS_HEADER),
    ("backup.py", 80, 333, COMMON),
    ("config.py", 334, 431, COMMON),
    (
        "module.py",
        433,
        497,
        COMMON + "from odoo_instance_sdk.models._literals import ModuleJsonValue\n\n",
    ),
    (
        "command.py",
        499,
        591,
        COMMON + "from odoo_instance_sdk.models.backup import Backup\n\n",
    ),
    ("runtime.py", 592, 666, COMMON),
    ("git.py", 668, 738, COMMON),
    (
        "footprint.py",
        739,
        814,
        COMMON
        + """from odoo_instance_sdk.models._literals import ClusterUnavailabilityReason, ServerUnavailabilityReason
from odoo_instance_sdk.models.backup import PostgresClusterState

""",
    ),
    (
        "postgres.py",
        815,
        1149,
        COMMON
        + """from odoo_instance_sdk.models._literals import ServerUnavailabilityReason
from odoo_instance_sdk.models.backup import PostgresClusterState

""",
    ),
    (
        "monitor.py",
        1150,
        1303,
        COMMON
        + """from odoo_instance_sdk.models._literals import ClusterUnavailabilityReason, ServerUnavailabilityReason
from odoo_instance_sdk.models.backup import EnvironmentState, PostgresClusterState
from odoo_instance_sdk.models.footprint import (
    ClusterContainer,
    ClusterEndpoint,
    ClusterMetrics,
    ClusterResourceSnapshot,
    EnvironmentArtifacts,
    PostgresServerInfo,
    RuntimeMetrics,
    StorageFootprint,
)
from odoo_instance_sdk.models.git import GitActivity
from odoo_instance_sdk.models.runtime import PgAdminEligibility, PortObservation, RuntimeState

""",
    ),
    (
        "process_inventory.py",
        1305,
        1532,
        COMMON
        + """from odoo_instance_sdk.models.backup import EnvironmentState
from odoo_instance_sdk.models.footprint import StorageFootprint
from odoo_instance_sdk.models.monitor import ClusterSnapshot
from odoo_instance_sdk.models.runtime import PidScope, RuntimeState

""",
    ),
]

SUBMODULES = [
    "backup",
    "command",
    "config",
    "footprint",
    "git",
    "module",
    "monitor",
    "postgres",
    "process_inventory",
    "runtime",
]


def main() -> None:
    lines = MODELS_PY.read_text().splitlines(keepends=True)
    MODELS_DIR.mkdir(exist_ok=True)

    for filename, start, end, header in SPLITS:
        body = "".join(lines[start - 1 : end])
        (MODELS_DIR / filename).write_text(header + body)

    init_lines = [
        '"""Public typed models re-exported from domain submodules."""',
        "",
        "from __future__ import annotations",
        "",
    ]
    for mod in SUBMODULES:
        init_lines.append(f"from odoo_instance_sdk.models.{mod} import *  # noqa: F403")
    init_lines.append("")
    (MODELS_DIR / "__init__.py").write_text("\n".join(init_lines) + "\n")
    MODELS_PY.unlink()
    print(f"Created models/ package with {len(SPLITS)} submodules")


if __name__ == "__main__":
    main()
