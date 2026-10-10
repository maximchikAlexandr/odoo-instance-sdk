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
        ("src/odoo_instance_sdk/commands/cli_parts/callbacks.py", 555),
        ("src/odoo_instance_sdk/commands/cli_parts/callbacks.py", 556),
        ("src/odoo_instance_sdk/commands/cli_parts/registration.py", 476),
        ("src/odoo_instance_sdk/commands/contract.py", 23),
        ("src/odoo_instance_sdk/commands/operation.py", 83),
        ("src/odoo_instance_sdk/commands/backup.py", 355),
        ("src/odoo_instance_sdk/commands/output.py", 115),
        ("src/odoo_instance_sdk/commands/output.py", 289),
        ("src/odoo_instance_sdk/commands/output.py", 458),
        ("src/odoo_instance_sdk/commands/output.py", 460),
        ("src/odoo_instance_sdk/commands/output.py", 467),
        ("src/odoo_instance_sdk/commands/output.py", 469),
        ("src/odoo_instance_sdk/internal/self_update.py", 801),
        ("src/odoo_instance_sdk/internal/self_update.py", 802),
        ("src/odoo_instance_sdk/internal/self_update.py", 803),
        ("src/odoo_instance_sdk/resources/instance/identity.py", 511),
    }
)


OUTPUT_WRITE_REASONS: Final[dict[SourceLocation, str]] = {
    ("src/odoo_instance_sdk/commands/output.py", 115): "in-memory Rich serialization boundary",
    ("src/odoo_instance_sdk/commands/output.py", 289): "shared Rich output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 458): "shared JSON output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 460): "shared TOON output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 467): "shared diagnostic boundary",
    ("src/odoo_instance_sdk/commands/output.py", 469): "shared diagnostic boundary",
    (
        "src/odoo_instance_sdk/commands/cli_parts/callbacks.py",
        555,
    ): "documented logs JSONL transport",
    (
        "src/odoo_instance_sdk/commands/cli_parts/callbacks.py",
        556,
    ): "documented logs JSONL transport",
    (
        "src/odoo_instance_sdk/commands/cli_parts/registration.py",
        476,
    ): "documented --version metadata flag transport",
    ("src/odoo_instance_sdk/commands/contract.py", 23): "metadata-only contract export transport",
    ("src/odoo_instance_sdk/commands/operation.py", 83): "bounded JSONL session transport",
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
        511,
    ): "lifecycle cleanup diagnostic transport",
}


# Keep only deliberate, line-specific exceptions in this checked inventory so
# every future regression reports its exact file and line instead of being
# hidden by a broad allowlist.
EXPLICIT_IMPRECISE_ANNOTATIONS: Final[dict[str, frozenset[int]]] = {
    "src/odoo_instance_sdk/commands/operation.py": frozenset({52, 67}),
    "src/odoo_instance_sdk/internal/dbreplace_recovery.py": frozenset(
        {57, 141, 150, 151, 167, 168}
    ),
    "src/odoo_instance_sdk/operations/builtin_models.py": frozenset(
        {17, 22, 330, 332, 356, 392, 515}
    ),
    "src/odoo_instance_sdk/operations/contracts.py": frozenset(
        {85, 231, 300, 396, 444, 465, 477, 478, 519, 521, 530, 554, 558, 563, 568, 591, 596}
    ),
    "src/odoo_instance_sdk/operations/invoke.py": frozenset({61, 78, 104}),
    "src/odoo_instance_sdk/resources/database/backup_restore_parts/backup.py": frozenset({71}),
    "src/odoo_instance_sdk/resources/monitor/collection_parts/collect.py": frozenset({526}),
}


MODULE_LOCAL_SUBPROCESS_PATCHES: Final[frozenset[SourceLocation]] = frozenset(
    {
        ("tests/unit/resources/test_database_resource.py", 807),
        ("tests/unit/test_monitor_cache_and_docker.py", 119),
        ("tests/unit/test_cluster_resources.py", 188),
        ("tests/unit/test_real_odoo_ci_components.py", 40),
        ("tests/unit/test_real_odoo_ci_components.py", 109),
        ("tests/unit/test_real_odoo_ci_components.py", 151),
        ("tests/unit/test_real_odoo_foundation.py", 333),
        ("tests/unit/test_real_odoo_foundation.py", 356),
        ("tests/unit/test_real_odoo_foundation.py", 375),
        ("tests/unit/test_mutmut_results.py", 32),
        ("tests/unit/test_mutmut_results.py", 47),
        ("tests/unit/test_odcli_autonomous_skill.py", 56),
        ("tests/unit/test_cli_surface.py", 58),
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
