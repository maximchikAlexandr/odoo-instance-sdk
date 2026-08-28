"""Reviewed JSON-safe fixtures for the CLI envelope and snapshot boundary.

The examples exercise the supported TOON boundary described by TOON
specification v4.1 (2026-07-26): nested objects, uniform arrays, nulls,
booleans, numbers, empty collections, and escaped strings.
"""

from __future__ import annotations

from typing import Any

CLI_ENVELOPE_V1_SUCCESS: dict[str, Any] = {
    "schema_version": 1,
    "ok": True,
    "command": "env.list",
    "context": {"project_id": "project_demo", "environment_id": None},
    "provenance": {"project_source": "cwd", "environment_source": "null"},
    "dry_run": False,
    "warnings": [],
    "result": {
        "schema_version": 3,
        "projects": [
            {
                "id": "project_demo",
                "name": 'Demo "Project"',
                "environments": [
                    {
                        "id": "env-1",
                        "name": "feature/emoji-✓",
                        "lifecycle_state": "active",
                        "allocated_http_port": 8069,
                        "observed_port": "free",
                        "artifacts": {
                            "worktree_exists": True,
                            "worktree_registered": True,
                            "config_exists": True,
                            "python_exists": True,
                            "python_contained": True,
                            "dependency_lock_exists": False,
                            "backup_exists": None,
                        },
                        "pgadmin": {"state": "eligible"},
                    }
                ],
            }
        ],
        "empty": [],
        "nullable": None,
        "count": 1.5,
        "enabled": True,
    },
    "data": {
        "schema_version": 3,
        "projects": [
            {
                "id": "project_demo",
                "name": 'Demo "Project"',
                "environments": [
                    {
                        "id": "env-1",
                        "name": "feature/emoji-✓",
                        "lifecycle_state": "active",
                        "allocated_http_port": 8069,
                        "observed_port": "free",
                        "artifacts": {
                            "worktree_exists": True,
                            "worktree_registered": True,
                            "config_exists": True,
                            "python_exists": True,
                            "python_contained": True,
                            "dependency_lock_exists": False,
                            "backup_exists": None,
                        },
                        "pgadmin": {"state": "eligible"},
                    }
                ],
            }
        ],
        "empty": [],
        "nullable": None,
        "count": 1.5,
        "enabled": True,
    },
}

CLI_ENVELOPE_V1_ERROR: dict[str, Any] = {
    "schema_version": 1,
    "ok": False,
    "command": "env.checkout",
    "context": {},
    "provenance": {},
    "dry_run": False,
    "warnings": [],
    "error": {
        "code": "env_checkout_failed",
        "message": "cannot open config: secret=*** [token=***]",
    },
}


OUTPUT_FIXTURES = (CLI_ENVELOPE_V1_SUCCESS, CLI_ENVELOPE_V1_ERROR)
