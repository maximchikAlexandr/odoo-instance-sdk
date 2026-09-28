"""Checked baseline for the execution-boundary migration.

The entries in this module are deliberately line-specific.  The architecture
tests compare the repository's current AST/source findings with this snapshot;
each migration phase must remove its entries instead of silently growing a
second, undocumented exception list.
"""

from __future__ import annotations

from typing import Final

SourceLocation = tuple[str, int]


DIRECT_SUBPROCESS_LAUNCHES: Final[frozenset[SourceLocation]] = frozenset({})


DIRECT_OUTPUT_WRITES: Final[frozenset[SourceLocation]] = frozenset(
    {
        ("src/odoo_instance_sdk/commands/cli_parts/callbacks.py", 539),
        ("src/odoo_instance_sdk/commands/cli_parts/callbacks.py", 540),
        ("src/odoo_instance_sdk/commands/cli_parts/registration.py", 474),
        ("src/odoo_instance_sdk/commands/backup.py", 355),
        ("src/odoo_instance_sdk/commands/output.py", 115),
        ("src/odoo_instance_sdk/commands/output.py", 287),
        ("src/odoo_instance_sdk/commands/output.py", 456),
        ("src/odoo_instance_sdk/commands/output.py", 458),
        ("src/odoo_instance_sdk/commands/output.py", 465),
        ("src/odoo_instance_sdk/commands/output.py", 467),
        ("src/odoo_instance_sdk/internal/self_update.py", 801),
        ("src/odoo_instance_sdk/internal/self_update.py", 802),
        ("src/odoo_instance_sdk/internal/self_update.py", 803),
        ("src/odoo_instance_sdk/resources/instance/identity.py", 503),
    }
)


OUTPUT_WRITE_REASONS: Final[dict[SourceLocation, str]] = {
    ("src/odoo_instance_sdk/commands/output.py", 115): "in-memory Rich serialization boundary",
    ("src/odoo_instance_sdk/commands/output.py", 287): "shared Rich output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 456): "shared JSON output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 458): "shared TOON output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 465): "shared diagnostic boundary",
    ("src/odoo_instance_sdk/commands/output.py", 467): "shared diagnostic boundary",
    (
        "src/odoo_instance_sdk/commands/cli_parts/callbacks.py",
        539,
    ): "documented logs JSONL transport",
    (
        "src/odoo_instance_sdk/commands/cli_parts/callbacks.py",
        540,
    ): "documented logs JSONL transport",
    (
        "src/odoo_instance_sdk/commands/cli_parts/registration.py",
        474,
    ): "documented --version metadata flag transport",
    ("src/odoo_instance_sdk/commands/backup.py", 355): "shared Rich output boundary",
    (
        "src/odoo_instance_sdk/internal/self_update.py",
        801,
    ): "maintenance child JSON stdout transport",
    (
        "src/odoo_instance_sdk/internal/self_update.py",
        802,
    ): "maintenance child JSON stdout transport",
    (
        "src/odoo_instance_sdk/internal/self_update.py",
        803,
    ): "maintenance child JSON stdout transport",
    (
        "src/odoo_instance_sdk/resources/instance/identity.py",
        503,
    ): "lifecycle cleanup diagnostic transport",
}


# Keep only deliberate, line-specific exceptions in this checked inventory so
# every future regression reports its exact file and line instead of being
# hidden by a broad allowlist.
EXPLICIT_IMPRECISE_ANNOTATIONS: Final[dict[str, frozenset[int]]] = {}


MODULE_LOCAL_SUBPROCESS_PATCHES: Final[frozenset[SourceLocation]] = frozenset(
    {
        ("tests/unit/resources/test_database_resource.py", 634),
        ("tests/unit/test_monitor_cache_and_docker.py", 119),
        ("tests/unit/test_cluster_resources.py", 188),
        ("tests/unit/test_real_odoo_ci_components.py", 40),
        ("tests/unit/test_real_odoo_ci_components.py", 109),
        ("tests/unit/test_real_odoo_ci_components.py", 151),
        ("tests/unit/test_real_odoo_foundation.py", 324),
        ("tests/unit/test_real_odoo_foundation.py", 347),
        ("tests/unit/test_real_odoo_foundation.py", 366),
        ("tests/unit/test_mutmut_results.py", 32),
        ("tests/unit/test_mutmut_results.py", 47),
    }
)


PUBLIC_PROCESS_METHODS: Final[dict[str, int]] = {}


DIRECT_HTTPX_USAGE: Final[frozenset[SourceLocation]] = frozenset(
    {
        ("src/odoo_instance_sdk/internal/transport/base.py", 203),
        ("src/odoo_instance_sdk/internal/transport/base.py", 208),
        ("src/odoo_instance_sdk/internal/transport/base.py", 233),
        ("src/odoo_instance_sdk/internal/transport/base.py", 235),
        ("src/odoo_instance_sdk/internal/transport/odoo.py", 77),
        ("src/odoo_instance_sdk/internal/transport/odoo.py", 79),
        ("src/odoo_instance_sdk/internal/transport/odoo.py", 156),
        ("src/odoo_instance_sdk/internal/transport/odoo.py", 158),
        ("src/odoo_instance_sdk/internal/transport/odoo.py", 159),
        ("src/odoo_instance_sdk/internal/transport/odoo.py", 210),
        ("src/odoo_instance_sdk/internal/transport/odoo.py", 213),
        ("src/odoo_instance_sdk/internal/transport/odoo.py", 215),
    }
)
