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
        ("src/odoo_instance_sdk/cli.py", 675),
        ("src/odoo_instance_sdk/cli.py", 676),
        ("src/odoo_instance_sdk/commands/env.py", 381),
        ("src/odoo_instance_sdk/commands/output.py", 184),
        ("src/odoo_instance_sdk/commands/output.py", 282),
        ("src/odoo_instance_sdk/commands/output.py", 284),
        ("src/odoo_instance_sdk/commands/output.py", 291),
        ("src/odoo_instance_sdk/commands/output.py", 293),
        ("src/odoo_instance_sdk/resources/instance.py", 661),
    }
)


OUTPUT_WRITE_REASONS: Final[dict[SourceLocation, str]] = {
    ("src/odoo_instance_sdk/commands/env.py", 381): "existing Rich live inventory transport",
    ("src/odoo_instance_sdk/commands/output.py", 184): "shared Rich output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 282): "shared JSON output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 284): "shared TOON output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 291): "shared diagnostic boundary",
    ("src/odoo_instance_sdk/commands/output.py", 293): "shared diagnostic boundary",
    ("src/odoo_instance_sdk/cli.py", 675): "documented logs JSONL transport",
    ("src/odoo_instance_sdk/cli.py", 676): "documented logs JSONL transport",
    ("src/odoo_instance_sdk/resources/instance.py", 661): "lifecycle cleanup diagnostic transport",
}


# A location is enough here: the AST assertion reports the concrete Any or
# object expression while this snapshot remains stable if one annotation has
# both forms (for example dict[str, Any] and an object-valued parameter).
EXPLICIT_IMPRECISE_ANNOTATIONS: Final[dict[str, frozenset[int]]] = {
    "src/odoo_instance_sdk/__init__.py": frozenset({354}),
    "src/odoo_instance_sdk/cli.py": frozenset(
        {86, 119, 123, 127, 170, 410, 530, 531, 540, 601, 752, 816, 1008, 1009, 1011, 1276, 1387}
    ),
    "src/odoo_instance_sdk/commands/context.py": frozenset({61, 62, 113, 117, 121}),
    "src/odoo_instance_sdk/commands/db.py": frozenset({184, 196, 200, 204}),
    "src/odoo_instance_sdk/commands/env.py": frozenset({579, 583, 587, 591, 835, 848, 860, 871}),
    "src/odoo_instance_sdk/commands/output.py": frozenset({23}),
    "src/odoo_instance_sdk/commands/test.py": frozenset(
        {66, 67, 91, 92, 94, 108, 124, 125, 126, 150, 154, 191, 198, 199, 200, 388, 389}
    ),
    "src/odoo_instance_sdk/exceptions.py": frozenset({250, 251}),
    "src/odoo_instance_sdk/http/app.py": frozenset({52, 68, 86}),
    "src/odoo_instance_sdk/http/monitor.py": frozenset(
        {33, 39, 49, 51, 66, 74, 82, 119, 143, 157, 188, 224, 228, 231}
    ),
    "src/odoo_instance_sdk/internal/automation.py": frozenset(
        {36, 234, 429, 433, 733, 742, 893, 896, 952}
    ),
    "src/odoo_instance_sdk/internal/cluster_resources.py": frozenset(
        {92, 93, 106, 124, 125, 143, 193, 205, 396, 422, 423}
    ),
    "src/odoo_instance_sdk/internal/context.py": frozenset({99, 100, 141}),
    "src/odoo_instance_sdk/internal/git_worktree.py": frozenset({164}),
    "src/odoo_instance_sdk/internal/pgadmin.py": frozenset({12, 14, 15, 16}),
    "src/odoo_instance_sdk/internal/pgadmin_container.py": frozenset(
        {
            55,
            56,
            85,
            86,
            87,
            132,
            133,
            162,
            163,
            171,
            174,
            241,
            248,
            256,
            264,
            270,
            271,
            288,
            289,
            314,
            331,
            338,
            342,
            343,
            385,
            394,
            404,
            410,
            411,
            464,
            465,
            491,
            492,
            523,
            527,
        }
    ),
    "src/odoo_instance_sdk/internal/pgadmin_files.py": frozenset({158, 312}),
    "src/odoo_instance_sdk/internal/pgadmin_readiness.py": frozenset({14}),
    "src/odoo_instance_sdk/internal/port_allocation.py": frozenset({118}),
    "src/odoo_instance_sdk/internal/postgres_compose.py": frozenset({125, 362, 381}),
    "src/odoo_instance_sdk/internal/process_metrics.py": frozenset({24, 39}),
    "src/odoo_instance_sdk/internal/server.py": frozenset({318}),
    "src/odoo_instance_sdk/internal/vscode_generate.py": frozenset({20, 92}),
    "src/odoo_instance_sdk/internal/vscode_import.py": frozenset(
        {36, 73, 88, 89, 111, 112, 138, 139, 284, 301, 318}
    ),
    "src/odoo_instance_sdk/project.py": frozenset(
        {97, 108, 110, 113, 114, 192, 215, 252, 258, 267, 273, 281, 292}
    ),
    "src/odoo_instance_sdk/resources/environment.py": frozenset(
        {
            143,
            793,
            815,
            817,
            947,
            955,
            958,
            1058,
            1061,
            1177,
            1580,
            1585,
            1722,
            2090,
            2092,
            2151,
            2153,
            2170,
            2172,
            2320,
            2324,
            2392,
            2393,
            2400,
            2407,
            2442,
            2445,
            2450,
        }
    ),
    "src/odoo_instance_sdk/resources/postgres.py": frozenset({805}),
    "src/odoo_instance_sdk/storage/backup_catalog.py": frozenset({713, 781}),
}


MODULE_LOCAL_SUBPROCESS_PATCHES: Final[frozenset[SourceLocation]] = frozenset(
    {
        ("tests/unit/internal/test_pgadmin_files.py", 333),
        ("tests/unit/internal/test_pgadmin_files.py", 392),
        ("tests/unit/internal/test_postgres_size.py", 28),
        ("tests/unit/internal/test_postgres_size.py", 55),
        ("tests/unit/internal/test_postgres_size.py", 78),
        ("tests/unit/internal/test_postgres_size.py", 109),
        ("tests/unit/internal/test_postgres_transport.py", 25),
        ("tests/unit/internal/test_postgres_transport.py", 72),
        ("tests/unit/internal/test_postgres_transport.py", 89),
        ("tests/unit/internal/test_postgres_transport.py", 110),
        ("tests/unit/internal/test_postgres_transport.py", 132),
        ("tests/unit/internal/test_postgres_transport.py", 175),
        ("tests/unit/resources/test_cli_automation.py", 606),
        ("tests/unit/resources/test_database_resource.py", 395),
        ("tests/unit/resources/test_database_resource.py", 421),
        ("tests/unit/resources/test_database_resource.py", 448),
        ("tests/unit/resources/test_database_resource.py", 473),
        ("tests/unit/resources/test_database_resource.py", 518),
        ("tests/unit/resources/test_database_resource.py", 539),
        ("tests/unit/resources/test_database_resource.py", 558),
        ("tests/unit/resources/test_database_resource.py", 573),
        ("tests/unit/resources/test_database_resource.py", 585),
        ("tests/unit/resources/test_database_resource.py", 599),
        ("tests/unit/resources/test_database_resource.py", 617),
        ("tests/unit/resources/test_environment_python.py", 42),
        ("tests/unit/resources/test_environment_python.py", 233),
        ("tests/unit/resources/test_environment_python.py", 271),
        ("tests/unit/test_monitor_cache_and_docker.py", 128),
    }
)


PUBLIC_PROCESS_METHODS: Final[dict[str, int]] = {}
