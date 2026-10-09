"""Production-owned public operation inventory.

The inventory is data-only: importing it never resolves a project or imports
operation callbacks.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

LeafCase = TypeVar("LeafCase")


_ALIASES: dict[tuple[str, ...], tuple[tuple[str, ...], ...]] = {
    ("env", "create"): (("env", "checkout"),),
    ("env", "ls"): (("env", "list"),),
    ("env", "rm"): (("env", "remove"),),
    ("backup", "ls"): (("backup", "list"),),
    ("backup", "inspect"): (("backup", "show"),),
    ("backup", "rm"): (("backup", "delete"),),
    ("db", "ls"): (("db", "list"),),
    ("db", "rm"): (("db", "drop"),),
    ("postgres", "ps"): (("postgres", "status"),),
    ("resource", "ls"): (("resource", "list"),),
    ("module", "ls"): (("module", "list"),),
    ("remote", "ls"): (("remote", "list"),),
}


# This is intentionally a data-only projection. It replaces the test-owned
# table as the runtime authority without importing command callbacks.
_LEAF_ROWS: tuple[tuple[tuple[str, ...], str, bool, str | None], ...] = (
    (("init",), "mutating-or-spawning", True, "init_project_command"),
    (("doctor",), "bounded-read-only", False, "run_doctor"),
    (("stop",), "mutating-or-spawning", True, "OdooInstance.stop_runtime_command"),
    (("resource", "ls"), "bounded-read-only", False, None),
    (("resource", "doctor"), "bounded-read-only", False, None),
    (("env", "create"), "mutating-or-spawning", True, "EnvironmentResource.checkout_command"),
    (("env", "ls"), "bounded-read-only", False, "EnvironmentResource.checkout_inventory_command"),
    (("env", "path"), "bounded-read-only", False, None),
    (("env", "show"), "bounded-read-only", False, None),
    (("env", "rm"), "mutating-or-spawning", True, "EnvironmentResource.remove_command"),
    (("env", "sync"), "mutating-or-spawning", True, "EnvironmentResource.sync_python_command"),
    (("remote", "ls"), "bounded-read-only", False, "list_remote_sources"),
    (("remote", "add"), "mutating-or-spawning", True, "configure_remote_source_command"),
    (("remote", "update"), "mutating-or-spawning", True, "configure_remote_source_command"),
    (("remote", "remove"), "mutating-or-spawning", True, "remove_remote_source_command"),
    (("backup", "ls"), "bounded-read-only", False, None),
    (("backup", "retention"), "mutating-or-spawning", True, "BackupResource.set_retention_command"),
    (("backup", "pin"), "mutating-or-spawning", True, "BackupResource.set_pinned_command"),
    (("backup", "unpin"), "mutating-or-spawning", True, "BackupResource.set_pinned_command"),
    (("backup", "prune"), "mutating-or-spawning", True, "BackupResource.prune_command"),
    (("backup", "inspect"), "bounded-read-only", False, "BackupResource.inspect_command"),
    (("backup", "validate"), "bounded-read-only", False, "BackupResource.validate_command"),
    (("backup", "rm"), "mutating-or-spawning", True, "BackupResource.delete_command"),
    (
        ("db", "refresh"),
        "mutating-or-spawning",
        True,
        "EnvironmentResource.refresh_database_command",
    ),
    (
        ("db", "restore"),
        "mutating-or-spawning",
        True,
        "EnvironmentResource.refresh_database_command",
    ),
    (("db", "ls"), "bounded-read-only", False, "DatabaseResource.list_inventory_command"),
    (
        ("db", "reset-admin-password"),
        "mutating-or-spawning",
        True,
        "DatabaseResource.reset_admin_password_command",
    ),
    (("db", "rm"), "mutating-or-spawning", True, None),
    (("eval",), "process-previewable-read-only", True, "eval_expression_command"),
    (("exec",), "mutating-or-spawning", True, "exec_script_command"),
    (("test",), "process-previewable-read-only", True, "run_odoo_tests_command"),
    (("module", "ls"), "process-previewable-read-only", True, "list_modules_command"),
    (("module", "update"), "mutating-or-spawning", True, "ModuleResource.update_command"),
    (("module", "test"), "mutating-or-spawning", True, "run_odoo_tests_command"),
    (("module", "info"), "bounded-read-only", False, "ModuleResource.info"),
    (("module", "where"), "bounded-read-only", False, "ModuleResource.where"),
    (("module", "deps"), "bounded-read-only", False, "ModuleResource.dependencies"),
    (
        ("module", "install-order"),
        "process-previewable-read-only",
        True,
        "ModuleResource.install_order_command",
    ),
    (("translations", "export"), "mutating-or-spawning", True, "export_translations_command"),
    (("deps", "verify"), "process-previewable-read-only", True, "verify_deps_command"),
    (("vscode", "generate"), "mutating-or-spawning", True, None),
    (
        ("postgres", "approve-image"),
        "mutating-or-spawning",
        True,
        "PostgresCluster.approve_image_command",
    ),
    (("postgres", "ps"), "bounded-read-only", False, "PostgresCluster.status_command"),
    (("postgres", "up"), "mutating-or-spawning", True, "PostgresCluster.ensure_running_command"),
    (("postgres", "stop"), "mutating-or-spawning", True, "PostgresCluster.stop_command"),
    (("db", "locks"), "bounded-read-only", False, "DatabaseResource.locks_command"),
    (("db", "stats"), "bounded-read-only", False, "DatabaseResource.stats_command"),
    (("db", "bloat"), "bounded-read-only", False, "DatabaseResource.bloat_command"),
    (
        ("db", "init-monitoring"),
        "mutating-or-spawning",
        True,
        "DatabaseResource.init_monitoring_command",
    ),
    (("psql",), "native-passthrough", True, None),
    (("run",), "native-passthrough", True, None),
    (("logs",), "jsonl-stream", False, None),
    (("shell",), "native-passthrough", True, None),
    (("monitor",), "native-passthrough", False, None),
    (("git", "commit"), "mutating-or-spawning", True, "GitResource.commit_command"),
    (("git", "check"), "bounded-read-only", False, "GitResource.check_command"),
    (("git", "absorb"), "mutating-or-spawning", True, "GitResource.absorb_command"),
    (("git", "sync"), "mutating-or-spawning", True, "GitResource.sync_command"),
    (("ps",), "bounded-read-only", False, "EnvironmentMonitor.processes_command"),
    (("bug-report", "init"), "mutating-or-spawning", True, "bug_report_init_command"),
    (("bug-report", "submit"), "mutating-or-spawning", True, "bug_report_submit_command"),
    (("update",), "mutating-or-spawning", True, "update_command"),
    (("contract", "export"), "bounded-read-only", False, None),
)


def build_public_leaf_cases(factory: Callable[..., LeafCase]) -> tuple[LeafCase, ...]:
    """Instantiate the inventory with the public contract row type."""
    return tuple(
        factory(
            path=path,
            classification=classification,
            requires_dry_run=requires_dry_run,
            sdk_primitive=sdk_primitive,
            cli_only_reason=("CLI-only composition projection" if sdk_primitive is None else None),
            aliases=_ALIASES.get(path, ()),
        )
        for path, classification, requires_dry_run, sdk_primitive in _LEAF_ROWS
    )
