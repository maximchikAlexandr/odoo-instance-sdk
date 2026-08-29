"""Checked baseline for the execution-boundary migration.

The entries in this module are deliberately line-specific.  The architecture
tests compare the repository's current AST/source findings with this snapshot;
each migration phase must remove its entries instead of silently growing a
second, undocumented exception list.
"""

from __future__ import annotations

from typing import Final

SourceLocation = tuple[str, int]


DIRECT_SUBPROCESS_LAUNCHES: Final[frozenset[SourceLocation]] = frozenset(
    {
        ("src/odoo_instance_sdk/internal/automation.py", 523),
        ("src/odoo_instance_sdk/internal/automation.py", 540),
        ("src/odoo_instance_sdk/internal/backup_validation.py", 85),
        ("src/odoo_instance_sdk/internal/git_activity.py", 19),
        ("src/odoo_instance_sdk/internal/git_worktree.py", 28),
        ("src/odoo_instance_sdk/internal/pgadmin_files.py", 432),
        ("src/odoo_instance_sdk/internal/pgadmin_files.py", 462),
        ("src/odoo_instance_sdk/internal/postgres_compose.py", 71),
        ("src/odoo_instance_sdk/internal/postgres_transport.py", 64),
        ("src/odoo_instance_sdk/internal/storage_footprint.py", 38),
        ("src/odoo_instance_sdk/internal/test_selection.py", 430),
        ("src/odoo_instance_sdk/resources/environment.py", 906),
        ("src/odoo_instance_sdk/resources/environment.py", 933),
        ("src/odoo_instance_sdk/resources/environment.py", 965),
        ("src/odoo_instance_sdk/resources/environment.py", 1866),
        ("src/odoo_instance_sdk/resources/instance.py", 280),
        ("src/odoo_instance_sdk/resources/instance.py", 288),
    }
)


DIRECT_OUTPUT_WRITES: Final[frozenset[SourceLocation]] = frozenset(
    {
        ("src/odoo_instance_sdk/cli.py", 539),
        ("src/odoo_instance_sdk/cli.py", 567),
        ("src/odoo_instance_sdk/cli.py", 568),
        ("src/odoo_instance_sdk/cli.py", 686),
        ("src/odoo_instance_sdk/cli.py", 881),
        ("src/odoo_instance_sdk/commands/env.py", 329),
        ("src/odoo_instance_sdk/commands/output.py", 89),
        ("src/odoo_instance_sdk/commands/output.py", 167),
        ("src/odoo_instance_sdk/commands/output.py", 169),
        ("src/odoo_instance_sdk/commands/output.py", 200),
        ("src/odoo_instance_sdk/commands/test.py", 175),
        ("src/odoo_instance_sdk/resources/instance.py", 482),
    }
)


OUTPUT_WRITE_REASONS: Final[dict[SourceLocation, str]] = {
    ("src/odoo_instance_sdk/commands/output.py", 89): "shared Rich output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 167): "shared TOON output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 169): "shared JSON output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 200): "shared diagnostic boundary",
    ("src/odoo_instance_sdk/commands/env.py", 329): "existing Rich live inventory transport",
    ("src/odoo_instance_sdk/commands/test.py", 175): "operation diagnostic transport",
    ("src/odoo_instance_sdk/cli.py", 539): "native run port-conflict diagnostic",
    ("src/odoo_instance_sdk/cli.py", 567): "documented logs JSONL transport",
    ("src/odoo_instance_sdk/cli.py", 568): "documented logs JSONL transport",
    ("src/odoo_instance_sdk/cli.py", 686): "legacy exec native diagnostic transport",
    ("src/odoo_instance_sdk/cli.py", 881): "legacy module-test diagnostic transport",
    ("src/odoo_instance_sdk/resources/instance.py", 482): "lifecycle cleanup diagnostic transport",
}


# A location is enough here: the AST assertion reports the concrete Any or
# object expression while this snapshot remains stable if one annotation has
# both forms (for example dict[str, Any] and an object-valued parameter).
EXPLICIT_IMPRECISE_ANNOTATIONS: Final[dict[str, frozenset[int]]] = {
    "src/odoo_instance_sdk/__init__.py": frozenset({354}),
    "src/odoo_instance_sdk/cli.py": frozenset(
        {62, 95, 99, 103, 328, 448, 449, 458, 519, 780, 781, 783}
    ),
    "src/odoo_instance_sdk/commands/context.py": frozenset({61, 62, 113, 117, 121}),
    "src/odoo_instance_sdk/commands/db.py": frozenset({121, 133, 137, 141}),
    "src/odoo_instance_sdk/commands/env.py": frozenset({527, 531, 535, 539, 708, 721, 733}),
    "src/odoo_instance_sdk/commands/output.py": frozenset(
        {
            27,
            50,
            55,
            63,
            71,
            81,
            82,
            92,
            108,
            112,
            113,
            114,
            117,
            120,
            143,
            147,
            148,
            149,
            152,
            174,
            177,
        }
    ),
    "src/odoo_instance_sdk/commands/test.py": frozenset(
        {57, 58, 82, 83, 85, 99, 115, 116, 117, 141, 145, 178, 185, 186, 187, 349, 350}
    ),
    "src/odoo_instance_sdk/exceptions.py": frozenset({250, 251}),
    "src/odoo_instance_sdk/http/app.py": frozenset({52, 68, 86}),
    "src/odoo_instance_sdk/http/monitor.py": frozenset(
        {33, 39, 49, 51, 66, 74, 82, 119, 143, 157, 188, 224, 228, 231}
    ),
    "src/odoo_instance_sdk/internal/automation.py": frozenset(
        {32, 82, 222, 226, 339, 348, 450, 453, 509}
    ),
    "src/odoo_instance_sdk/internal/cluster_resources.py": frozenset(
        {92, 93, 106, 124, 125, 143, 193, 205, 396, 422, 423}
    ),
    "src/odoo_instance_sdk/internal/context.py": frozenset({99, 100, 141}),
    "src/odoo_instance_sdk/internal/git_worktree.py": frozenset({140}),
    "src/odoo_instance_sdk/internal/pgadmin.py": frozenset({12, 14, 15, 16}),
    "src/odoo_instance_sdk/internal/pgadmin_container.py": frozenset(
        {
            55,
            83,
            84,
            119,
            120,
            149,
            150,
            158,
            161,
            219,
            226,
            234,
            242,
            248,
            249,
            266,
            267,
            292,
            309,
            316,
            320,
            321,
            363,
            372,
            382,
            388,
            389,
            442,
            443,
            463,
            464,
            493,
            497,
        }
    ),
    "src/odoo_instance_sdk/internal/pgadmin_files.py": frozenset({158, 312}),
    "src/odoo_instance_sdk/internal/pgadmin_readiness.py": frozenset({14}),
    "src/odoo_instance_sdk/internal/port_allocation.py": frozenset({118}),
    "src/odoo_instance_sdk/internal/postgres_compose.py": frozenset({99, 336, 355}),
    "src/odoo_instance_sdk/internal/process_metrics.py": frozenset({24, 39}),
    "src/odoo_instance_sdk/internal/server.py": frozenset({309}),
    "src/odoo_instance_sdk/internal/vscode_generate.py": frozenset({20, 92}),
    "src/odoo_instance_sdk/internal/vscode_import.py": frozenset(
        {36, 73, 88, 89, 111, 112, 138, 139, 284, 301, 318}
    ),
    "src/odoo_instance_sdk/project.py": frozenset(
        {97, 108, 110, 113, 114, 192, 215, 252, 258, 267, 273, 281, 292}
    ),
    "src/odoo_instance_sdk/resources/environment.py": frozenset(
        {
            125,
            476,
            498,
            592,
            600,
            603,
            703,
            706,
            822,
            1064,
            1069,
            1118,
            1478,
            1480,
            1537,
            1539,
            1556,
            1558,
            1640,
            1699,
            1703,
            1771,
            1772,
            1779,
            1786,
            1821,
            1824,
            1829,
        }
    ),
    "src/odoo_instance_sdk/resources/postgres.py": frozenset({408}),
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
        ("tests/unit/resources/test_database_resource.py", 361),
        ("tests/unit/resources/test_database_resource.py", 387),
        ("tests/unit/resources/test_database_resource.py", 414),
        ("tests/unit/resources/test_database_resource.py", 439),
        ("tests/unit/resources/test_database_resource.py", 484),
        ("tests/unit/resources/test_database_resource.py", 505),
        ("tests/unit/resources/test_database_resource.py", 524),
        ("tests/unit/resources/test_database_resource.py", 539),
        ("tests/unit/resources/test_database_resource.py", 551),
        ("tests/unit/resources/test_database_resource.py", 565),
        ("tests/unit/resources/test_database_resource.py", 583),
        ("tests/unit/resources/test_environment_python.py", 42),
        ("tests/unit/resources/test_environment_python.py", 233),
        ("tests/unit/resources/test_environment_python.py", 271),
        ("tests/unit/resources/test_instance_runtime.py", 246),
        ("tests/unit/test_monitor_cache_and_docker.py", 128),
    }
)


PUBLIC_PROCESS_METHODS: Final[dict[str, int]] = {
    "execution.py:Command.run": 191,
    "internal/database_preparation.py:DatabasePreparationCoordinator.prepare": 703,
    "internal/database_preparation.py:DatabasePreparationCoordinator.refresh_database": 714,
    "internal/postgres_compose.py:SubprocessComposeRunner.run": 64,
    "resources/backup.py:BackupResource.delete": 83,
    "resources/backup.py:BackupResource.validate": 118,
    "resources/database.py:DatabaseResource.exists": 227,
    "resources/database.py:DatabaseResource.current": 265,
    "resources/database.py:DatabaseResource.backup": 314,
    "resources/database.py:DatabaseResource.reset_admin_password": 434,
    "resources/database.py:DatabaseResource.restore": 467,
    "resources/database.py:DatabaseResource.drop": 539,
    "resources/environment.py:EnvironmentResource.refresh_database": 291,
    "resources/environment.py:EnvironmentResource.plan_checkout": 421,
    "resources/environment.py:EnvironmentResource.checkout_with_plan": 433,
    "resources/environment.py:EnvironmentResource.checkout": 467,
    "resources/environment.py:EnvironmentResource.sync_python": 835,
    "resources/environment.py:EnvironmentResource.open_pgadmin": 984,
    "resources/environment.py:EnvironmentResource.list": 1082,
    "resources/environment.py:EnvironmentResource.remove": 1108,
    "resources/instance.py:InstanceFactory.from_environment": 108,
    "resources/instance.py:OdooInstance.run": 355,
    "resources/instance.py:OdooInstance.start": 371,
    "resources/instance.py:OdooInstance.run_foreground": 390,
    "resources/instance.py:OdooInstance.shell": 501,
    "resources/instance.py:OdooInstance.run_shell_script": 521,
    "resources/monitor.py:EnvironmentMonitor.snapshot": 216,
    "resources/monitor.py:EnvironmentMonitor.watch": 360,
    "resources/postgres.py:PostgresCluster.from_project": 92,
    "resources/postgres.py:PostgresCluster.resolve_image_digest": 212,
    "resources/postgres.py:PostgresCluster.approve_image": 218,
    "resources/postgres.py:PostgresCluster.status": 296,
    "resources/postgres.py:PostgresCluster.ensure_running": 324,
    "resources/postgres.py:PostgresCluster.stop": 380,
    "resources/postgres.py:PostgresCluster.resource_snapshot": 419,
}
