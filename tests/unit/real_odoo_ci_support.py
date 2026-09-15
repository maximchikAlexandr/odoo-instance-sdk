"""Shared builders for the real-Odoo CI contract tests."""

import json
from dataclasses import asdict
from pathlib import Path

from scripts import real_odoo_acceptance as acceptance
from scripts import real_odoo_bootstrap as bootstrap
from scripts.real_odoo_pins import E2E_PINS


def _evidence_contract(
    source: Path, *, junit_failures: int = 0, source_cache_consumed: bool = True
) -> None:
    (source / "bootstrap.json").write_text(
        json.dumps(
            {
                "ok": True,
                "platform": "linux/amd64",
                "architecture": "X64",
                "cache": {
                    "class": "cold",
                    "source_hit": False,
                    "uv_hit": False,
                    "source_key": "odoo19-Linux-X64-pin",
                    "uv_key": "uv-Linux-X64-python-uv-digest",
                },
                "pins": {"uv": "0.10.8"},
            }
        )
        + "\n"
    )
    (source / "resource-manifest.json").write_text(
        json.dumps(
            {
                "resources": ["owned-run-resource"],
                "audit": {
                    "state": "clean",
                    "leaks": [],
                    "runs": [{"run_id": "owned-run", "state": "clean", "leaks": {}}],
                },
                "source_cache_consumed": source_cache_consumed,
            }
        )
        + "\n"
    )
    (source / "command-matrix.md").write_text(
        "# Public CLI traceability matrix\n\n| Public leaf | Evidence |\n",
        encoding="utf-8",
    )
    (source / "timing.json").write_text(
        json.dumps(
            {
                "schema": "odcli-real-odoo-timing-v1",
                "phases": {
                    "setup": {"duration_seconds": 1.0},
                    "test": {"duration_seconds": 2.0},
                    "cleanup": {"duration_seconds": 0.1},
                },
            }
        )
    )
    (source / "junit.xml").write_text(f"<testsuite tests='1' failures='{junit_failures}'/>\n")


def _acceptance_run(root: Path, tier: str, cache_class: str, *, failed: bool = False) -> None:
    run = root / f"{tier}-{cache_class}"
    run.mkdir(parents=True)
    bootstrap_value: dict[str, object] = {
        "ok": True,
        "platform": "linux/amd64",
        "pins": asdict(E2E_PINS),
        "cache": {"class": cache_class},
    }
    if tier == "full":
        bootstrap_value["prerequisites"] = {
            "python_resolution_lock": True,
            "python_resolution_audit": True,
            "odoo_source_revision": True,
        }
    (run / "bootstrap.json").write_text(json.dumps(bootstrap_value))
    properties = "".join(
        f"<property name='{identifier.lower().replace('-', '_')}' value='passed'/>"
        for identifier in acceptance.REQUIRED_TIER_EVIDENCE[tier]
    )
    (run / "junit.xml").write_text(
        f"<testsuite tests='1' failures='{int(failed)}' errors='0'>"
        f"<properties>{properties}</properties></testsuite>\n"
    )
    (run / "timing.json").write_text(
        json.dumps(
            {
                "phases": {
                    "setup": {"duration_seconds": 1.0},
                    "test": {"duration_seconds": 1.0},
                    "cleanup": {"duration_seconds": 1.0},
                },
                "budget": {
                    "ok": not failed,
                    "artifact_bytes": 100,
                    "artifact_budget_bytes": acceptance.SUCCESS_ARTIFACT_LIMIT_BYTES,
                },
            }
        )
    )
    (run / "evidence-manifest.json").write_text(
        json.dumps(
            {
                "ok": not failed,
                "artifact_bytes": 100,
                "bundle_limit_bytes": acceptance.FAILURE_BUNDLE_LIMIT_BYTES,
                "success_limit_bytes": acceptance.SUCCESS_ARTIFACT_LIMIT_BYTES,
                "budget": {"ok": not failed, "artifact_bytes": 100},
            }
        )
    )
    (run / "resource-manifest.json").write_text(
        json.dumps(
            {
                "resources": ["owned"],
                "source_cache_consumed": True,
                "audit": {
                    "state": "failed" if failed else "clean",
                    "leaks": ["leak"] if failed else [],
                    "runs": [{"state": "failed" if failed else "clean"}],
                },
            }
        )
    )


def _complete_acceptance_evidence(root: Path) -> None:
    for tier, cache_class in acceptance.REQUIRED_RUNS:
        _acceptance_run(root, tier, cache_class)


def _fixture_audit_findings() -> set[tuple[str, str, str]]:
    value = json.loads(bootstrap.PYTHON_RESOLUTION_AUDIT.read_text(encoding="utf-8"))
    return {
        (item["package"], item["version"], advisory)
        for item in value["exceptions"]
        for advisory in item["advisories"]
    }
