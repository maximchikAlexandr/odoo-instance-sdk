"""Checked baseline for the execution-boundary migration.

The entries in this module are deliberately line-specific.  The architecture
tests compare the repository's current AST/source findings with this snapshot;
each migration phase must remove its entries instead of silently growing a
second, undocumented exception list.
"""

from __future__ import annotations

from typing import Final

SourceLocation = tuple[str, int]

ALTERNATE_LIFECYCLE_MODULE: Final = "src/odoo_instance_sdk/internal/alternate_tool.py"
ALTERNATE_COORDINATOR_MODULE: Final = "src/odoo_instance_sdk/internal/agent_checkout.py"
ALTERNATE_COORDINATOR_MECHANICS: Final[frozenset[str]] = frozenset(
    {"fcntl.flock(", "lifecycle_lock(", "os.replace(", "shutil.rmtree(", "tempfile."}
)
ALTERNATE_POLICY_SURFACES: Final[frozenset[str]] = frozenset(
    {
        "scripts/odcli_agent_tools/fix_tool.py",
        "scripts/odcli_agent_tools/numbered_tool.py",
    }
)
ALTERNATE_LIFECYCLE_MECHANIC_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "UV_TOOL_DIR",
        "UV_TOOL_BIN_DIR",
        "direct_url.json",
        "lifecycle.lock",
        "render_launcher(",
        "_provenance(",
        "_probe(",
        "lifecycle_lock(",
        "fcntl.flock(",
        "shutil.rmtree(",
        "os.replace(",
        "tempfile.",
    }
)


DIRECT_SUBPROCESS_LAUNCHES: Final[frozenset[SourceLocation]] = frozenset({})


DIRECT_OUTPUT_WRITES: Final[frozenset[SourceLocation]] = frozenset(
    {
        ("src/odoo_instance_sdk/commands/cli_parts/callbacks.py", 563),
        ("src/odoo_instance_sdk/commands/cli_parts/callbacks.py", 564),
        ("src/odoo_instance_sdk/commands/cli_parts/registration.py", 479),
        ("src/odoo_instance_sdk/commands/backup.py", 355),
        ("src/odoo_instance_sdk/commands/output.py", 115),
        ("src/odoo_instance_sdk/commands/output.py", 287),
        ("src/odoo_instance_sdk/commands/output.py", 502),
        ("src/odoo_instance_sdk/commands/output.py", 504),
        ("src/odoo_instance_sdk/commands/output.py", 511),
        ("src/odoo_instance_sdk/commands/output.py", 513),
        ("src/odoo_instance_sdk/internal/self_update.py", 822),
        ("src/odoo_instance_sdk/internal/self_update.py", 823),
        ("src/odoo_instance_sdk/internal/self_update.py", 824),
        ("src/odoo_instance_sdk/internal/git_credential_helper.py", 74),
        ("src/odoo_instance_sdk/internal/git_credential_helper.py", 75),
        ("src/odoo_instance_sdk/internal/git_askpass.py", 72),
        ("src/odoo_instance_sdk/resources/instance/identity.py", 508),
        ("src/odoo_instance_sdk/internal/alternate_tool.py", 980),
        ("src/odoo_instance_sdk/internal/alternate_tool.py", 982),
        ("src/odoo_instance_sdk/internal/alternate_tool.py", 984),
        ("src/odoo_instance_sdk/internal/alternate_tool.py", 990),
        ("src/odoo_instance_sdk/internal/alternate_tool.py", 993),
    }
)


OUTPUT_WRITE_REASONS: Final[dict[SourceLocation, str]] = {
    ("src/odoo_instance_sdk/commands/output.py", 115): "in-memory Rich serialization boundary",
    ("src/odoo_instance_sdk/commands/output.py", 287): "shared Rich output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 502): "shared JSON output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 504): "shared TOON output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 511): "shared diagnostic boundary",
    ("src/odoo_instance_sdk/commands/output.py", 513): "shared diagnostic boundary",
    (
        "src/odoo_instance_sdk/commands/cli_parts/callbacks.py",
        563,
    ): "documented logs JSONL transport",
    (
        "src/odoo_instance_sdk/commands/cli_parts/callbacks.py",
        564,
    ): "documented logs JSONL transport",
    (
        "src/odoo_instance_sdk/commands/cli_parts/registration.py",
        479,
    ): "documented --version metadata flag transport",
    ("src/odoo_instance_sdk/commands/backup.py", 355): "shared Rich output boundary",
    (
        "src/odoo_instance_sdk/internal/self_update.py",
        822,
    ): "maintenance child JSON stdout transport",
    (
        "src/odoo_instance_sdk/internal/self_update.py",
        823,
    ): "maintenance child JSON stdout transport",
    (
        "src/odoo_instance_sdk/internal/self_update.py",
        824,
    ): "maintenance child JSON stdout transport",
    (
        "src/odoo_instance_sdk/internal/git_credential_helper.py",
        74,
    ): "Git credential protocol child transport",
    (
        "src/odoo_instance_sdk/internal/git_credential_helper.py",
        75,
    ): "Git credential protocol child transport",
    ("src/odoo_instance_sdk/internal/git_askpass.py", 72): "Git askpass child transport",
    (
        "src/odoo_instance_sdk/resources/instance/identity.py",
        508,
    ): "lifecycle cleanup diagnostic transport",
    ("src/odoo_instance_sdk/internal/alternate_tool.py", 980): "alternate lifecycle JSON transport",
    ("src/odoo_instance_sdk/internal/alternate_tool.py", 982): "alternate lifecycle JSON transport",
    ("src/odoo_instance_sdk/internal/alternate_tool.py", 984): "alternate lifecycle JSON transport",
    ("src/odoo_instance_sdk/internal/alternate_tool.py", 990): "alternate lifecycle JSON transport",
    ("src/odoo_instance_sdk/internal/alternate_tool.py", 993): "alternate lifecycle JSON transport",
}


# Keep only deliberate, line-specific exceptions in this checked inventory so
# every future regression reports its exact file and line instead of being
# hidden by a broad allowlist.
MODULE_LOCAL_SUBPROCESS_PATCHES: Final[frozenset[SourceLocation]] = frozenset(
    {
        ("tests/unit/resources/test_database_resource.py", 1058),
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
        ("tests/unit/test_odcli_autonomous_skill.py", 178),
        ("tests/unit/project/test_prepare_venv.py", 271),
        ("tests/unit/test_gitlab_member_e2e_runner.py", 897),
        ("tests/unit/test_gitlab_member_e2e_runner.py", 952),
        ("tests/unit/test_gitlab_member_e2e_runner.py", 1394),
        ("tests/unit/test_gitlab_member_e2e_runner.py", 1451),
    }
)


PUBLIC_PROCESS_METHODS: Final[dict[str, int]] = {}


DIRECT_HTTPX_USAGE: Final[frozenset[SourceLocation]] = frozenset(
    {
        ("src/odoo_instance_sdk/internal/transport/base.py", 205),
        ("src/odoo_instance_sdk/internal/transport/base.py", 210),
        ("src/odoo_instance_sdk/internal/transport/base.py", 235),
        ("src/odoo_instance_sdk/internal/transport/base.py", 237),
        ("src/odoo_instance_sdk/internal/transport/gitlab.py", 33),
        ("src/odoo_instance_sdk/internal/transport/gitlab.py", 35),
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
