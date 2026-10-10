"""Production-owned public operation inventory.

The inventory is data-only: importing it never resolves a project or imports
operation callbacks. It is the single production source for public leaf
metadata and stable operation identities.
"""

from __future__ import annotations

from dataclasses import dataclass

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


@dataclass(frozen=True, slots=True)
class PublicLeafCase:
    """One canonical public Click leaf and its immutable operation identity."""

    operation_id: str
    path: tuple[str, ...]
    classification: str
    requires_dry_run: bool
    sdk_primitive: str | None = None
    cli_only_reason: str | None = None
    aliases: tuple[tuple[str, ...], ...] = ()

    def __post_init__(self) -> None:
        if not self.operation_id or "." not in self.operation_id:
            raise ValueError("operation_id must be a stable namespaced identifier")
        if not self.path or any(not part for part in self.path):
            raise ValueError("path must contain non-empty Click path parts")
        if self.sdk_primitive is None and not self.cli_only_reason:
            raise ValueError("leaf requires a concrete CLI-only transport or presentation reason")
        if self.sdk_primitive is not None and self.cli_only_reason is not None:
            raise ValueError("leaf requires exactly one implementation or CLI-only reason")
        if not self.aliases:
            object.__setattr__(self, "aliases", _ALIASES.get(self.path, ()))

    @property
    def is_bounded(self) -> bool:
        return self.classification in {
            "bounded-read-only",
            "process-previewable-read-only",
            "mutating-or-spawning",
        }


PUBLIC_LEAF_CASES: tuple[PublicLeafCase, ...] = (
    PublicLeafCase("odcli.init", ("init",), "mutating-or-spawning", True, "init_project_command"),
    PublicLeafCase("odcli.doctor", ("doctor",), "bounded-read-only", False, "run_doctor"),
    PublicLeafCase(
        "odcli.stop", ("stop",), "mutating-or-spawning", True, "OdooInstance.stop_runtime_command"
    ),
    PublicLeafCase("odcli.publish", ("publish",), "mutating-or-spawning", True, "publish_command"),
    PublicLeafCase(
        "odcli.unpublish", ("unpublish",), "mutating-or-spawning", True, "unpublish_command"
    ),
    PublicLeafCase(
        "odcli.resource.ls",
        ("resource", "ls"),
        "bounded-read-only",
        False,
        cli_only_reason="CLI-only resource inventory projection without a public SDK type",
    ),
    PublicLeafCase(
        "odcli.resource.doctor",
        ("resource", "doctor"),
        "bounded-read-only",
        False,
        cli_only_reason="CLI-only resource doctor projection without a public SDK type",
    ),
    PublicLeafCase(
        "odcli.env.create",
        ("env", "create"),
        "mutating-or-spawning",
        True,
        "EnvironmentResource.checkout_command",
    ),
    PublicLeafCase(
        "odcli.env.ls",
        ("env", "ls"),
        "bounded-read-only",
        False,
        "EnvironmentResource.checkout_inventory_command",
    ),
    PublicLeafCase(
        "odcli.env.path",
        ("env", "path"),
        "bounded-read-only",
        False,
        cli_only_reason="CLI-only worktree path resolution for shell substitution",
    ),
    PublicLeafCase(
        "odcli.env.show",
        ("env", "show"),
        "bounded-read-only",
        False,
        cli_only_reason="CLI-only Rich environment detail rendering",
    ),
    PublicLeafCase(
        "odcli.env.rm",
        ("env", "rm"),
        "mutating-or-spawning",
        True,
        "EnvironmentResource.remove_command",
    ),
    PublicLeafCase(
        "odcli.env.sync",
        ("env", "sync"),
        "mutating-or-spawning",
        True,
        "EnvironmentResource.sync_python_command",
    ),
    PublicLeafCase(
        "odcli.remote.ls", ("remote", "ls"), "bounded-read-only", False, "list_remote_sources"
    ),
    PublicLeafCase(
        "odcli.remote.add",
        ("remote", "add"),
        "mutating-or-spawning",
        True,
        "configure_remote_source_command",
    ),
    PublicLeafCase(
        "odcli.remote.update",
        ("remote", "update"),
        "mutating-or-spawning",
        True,
        "configure_remote_source_command",
    ),
    PublicLeafCase(
        "odcli.remote.remove",
        ("remote", "remove"),
        "mutating-or-spawning",
        True,
        "remove_remote_source_command",
    ),
    PublicLeafCase(
        "odcli.backup.ls",
        ("backup", "ls"),
        "bounded-read-only",
        False,
        cli_only_reason="CLI-only catalogue pagination and project-scope selection",
    ),
    PublicLeafCase(
        "odcli.backup.retention",
        ("backup", "retention"),
        "mutating-or-spawning",
        True,
        "BackupResource.set_retention_command",
    ),
    PublicLeafCase(
        "odcli.backup.pin",
        ("backup", "pin"),
        "mutating-or-spawning",
        True,
        "BackupResource.set_pinned_command",
    ),
    PublicLeafCase(
        "odcli.backup.unpin",
        ("backup", "unpin"),
        "mutating-or-spawning",
        True,
        "BackupResource.set_pinned_command",
    ),
    PublicLeafCase(
        "odcli.backup.prune",
        ("backup", "prune"),
        "mutating-or-spawning",
        True,
        "BackupResource.prune_command",
    ),
    PublicLeafCase(
        "odcli.backup.inspect",
        ("backup", "inspect"),
        "bounded-read-only",
        False,
        "BackupResource.inspect_command",
    ),
    PublicLeafCase(
        "odcli.backup.validate",
        ("backup", "validate"),
        "bounded-read-only",
        False,
        "BackupResource.validate_command",
    ),
    PublicLeafCase(
        "odcli.backup.rm",
        ("backup", "rm"),
        "mutating-or-spawning",
        True,
        "BackupResource.delete_command",
    ),
    PublicLeafCase(
        "odcli.db.refresh",
        ("db", "refresh"),
        "mutating-or-spawning",
        True,
        "EnvironmentResource.refresh_database_command",
    ),
    PublicLeafCase(
        "odcli.db.restore",
        ("db", "restore"),
        "mutating-or-spawning",
        True,
        "EnvironmentResource.refresh_database_command",
    ),
    PublicLeafCase(
        "odcli.db.ls",
        ("db", "ls"),
        "bounded-read-only",
        False,
        "DatabaseResource.list_inventory_command",
    ),
    PublicLeafCase(
        "odcli.db.reset_admin_password",
        ("db", "reset-admin-password"),
        "mutating-or-spawning",
        True,
        "DatabaseResource.reset_admin_password_command",
    ),
    PublicLeafCase(
        "odcli.db.rm",
        ("db", "rm"),
        "mutating-or-spawning",
        True,
        cli_only_reason="CLI-only database removal confirmation and lifecycle composition",
    ),
    PublicLeafCase(
        "odcli.eval", ("eval",), "mutating-or-spawning", True, "eval_expression_command"
    ),
    PublicLeafCase("odcli.exec", ("exec",), "mutating-or-spawning", True, "exec_script_command"),
    PublicLeafCase(
        "odcli.test", ("test",), "process-previewable-read-only", True, "run_odoo_tests_command"
    ),
    PublicLeafCase(
        "odcli.module.ls",
        ("module", "ls"),
        "process-previewable-read-only",
        True,
        "list_modules_command",
    ),
    PublicLeafCase(
        "odcli.module.update",
        ("module", "update"),
        "mutating-or-spawning",
        True,
        "ModuleResource.update_command",
    ),
    PublicLeafCase(
        "odcli.module.test",
        ("module", "test"),
        "mutating-or-spawning",
        True,
        "run_odoo_tests_command",
    ),
    PublicLeafCase(
        "odcli.module.info", ("module", "info"), "bounded-read-only", False, "ModuleResource.info"
    ),
    PublicLeafCase(
        "odcli.module.context",
        ("module", "context"),
        "bounded-read-only",
        False,
        "ModuleResource.context",
    ),
    PublicLeafCase(
        "odcli.module.where",
        ("module", "where"),
        "bounded-read-only",
        False,
        "ModuleResource.where",
    ),
    PublicLeafCase(
        "odcli.module.deps",
        ("module", "deps"),
        "bounded-read-only",
        False,
        "ModuleResource.dependencies",
    ),
    PublicLeafCase(
        "odcli.module.install_order",
        ("module", "install-order"),
        "process-previewable-read-only",
        True,
        "ModuleResource.install_order_command",
    ),
    PublicLeafCase(
        "odcli.translations.export",
        ("translations", "export"),
        "mutating-or-spawning",
        True,
        "export_translations_command",
    ),
    PublicLeafCase(
        "odcli.deps.verify",
        ("deps", "verify"),
        "process-previewable-read-only",
        True,
        "verify_deps_command",
    ),
    PublicLeafCase(
        "odcli.vscode.generate",
        ("vscode", "generate"),
        "mutating-or-spawning",
        True,
        cli_only_reason="CLI-only editor configuration file generation boundary",
    ),
    PublicLeafCase(
        "odcli.postgres.approve_image",
        ("postgres", "approve-image"),
        "mutating-or-spawning",
        True,
        "PostgresCluster.approve_image_command",
    ),
    PublicLeafCase(
        "odcli.postgres.ps",
        ("postgres", "ps"),
        "bounded-read-only",
        False,
        "PostgresCluster.status_command",
    ),
    PublicLeafCase(
        "odcli.postgres.up",
        ("postgres", "up"),
        "mutating-or-spawning",
        True,
        "PostgresCluster.ensure_running_command",
    ),
    PublicLeafCase(
        "odcli.postgres.stop",
        ("postgres", "stop"),
        "mutating-or-spawning",
        True,
        "PostgresCluster.stop_command",
    ),
    PublicLeafCase(
        "odcli.db.locks",
        ("db", "locks"),
        "bounded-read-only",
        False,
        "DatabaseResource.locks_command",
    ),
    PublicLeafCase(
        "odcli.db.stats",
        ("db", "stats"),
        "bounded-read-only",
        False,
        "DatabaseResource.stats_command",
    ),
    PublicLeafCase(
        "odcli.db.bloat",
        ("db", "bloat"),
        "bounded-read-only",
        False,
        "DatabaseResource.bloat_command",
    ),
    PublicLeafCase(
        "odcli.db.init_monitoring",
        ("db", "init-monitoring"),
        "mutating-or-spawning",
        True,
        "DatabaseResource.init_monitoring_command",
    ),
    PublicLeafCase(
        "odcli.psql",
        ("psql",),
        "native-passthrough",
        True,
        cli_only_reason="native psql terminal passthrough boundary",
    ),
    PublicLeafCase(
        "odcli.run",
        ("run",),
        "native-passthrough",
        True,
        cli_only_reason="native Odoo process passthrough boundary",
    ),
    PublicLeafCase(
        "odcli.logs",
        ("logs",),
        "jsonl-stream",
        False,
        cli_only_reason="JSONL streaming process output boundary",
    ),
    PublicLeafCase(
        "odcli.shell",
        ("shell",),
        "native-passthrough",
        True,
        cli_only_reason="interactive shell terminal passthrough boundary",
    ),
    PublicLeafCase(
        "odcli.monitor",
        ("monitor",),
        "native-passthrough",
        False,
        cli_only_reason="live monitor terminal presentation boundary",
    ),
    PublicLeafCase(
        "odcli.git.commit",
        ("git", "commit"),
        "mutating-or-spawning",
        True,
        "GitResource.commit_command",
    ),
    PublicLeafCase(
        "odcli.git.check", ("git", "check"), "bounded-read-only", False, "GitResource.check_command"
    ),
    PublicLeafCase(
        "odcli.git.absorb",
        ("git", "absorb"),
        "mutating-or-spawning",
        True,
        "GitResource.absorb_command",
    ),
    PublicLeafCase(
        "odcli.git.sync", ("git", "sync"), "mutating-or-spawning", True, "GitResource.sync_command"
    ),
    PublicLeafCase(
        "odcli.ps", ("ps",), "bounded-read-only", False, "EnvironmentMonitor.processes_command"
    ),
    PublicLeafCase(
        "odcli.bug_report.init",
        ("bug-report", "init"),
        "mutating-or-spawning",
        True,
        "bug_report_init_command",
    ),
    PublicLeafCase(
        "odcli.bug_report.submit",
        ("bug-report", "submit"),
        "mutating-or-spawning",
        True,
        "bug_report_submit_command",
    ),
    PublicLeafCase("odcli.update", ("update",), "mutating-or-spawning", True, "update_command"),
    PublicLeafCase(
        "odcli.contract.export",
        ("contract", "export"),
        "bounded-read-only",
        False,
        cli_only_reason="CLI-only canonical JSON-Schema export presentation boundary",
    ),
    PublicLeafCase(
        "odcli.operation.invoke",
        ("operation", "invoke"),
        "bounded-read-only",
        False,
        cli_only_reason="CLI-only local machine-operation dispatch boundary",
    ),
    PublicLeafCase(
        "odcli.operation.session",
        ("operation", "session"),
        "bounded-read-only",
        False,
        cli_only_reason="CLI-only bounded JSONL approval-session transport boundary",
    ),
)


__all__ = ["PUBLIC_LEAF_CASES", "PublicLeafCase"]
