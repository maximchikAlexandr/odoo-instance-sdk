#!/usr/bin/env python3
"""Generate the static and prerequisite evidence for the real-Odoo WP."""

from __future__ import annotations

import argparse
import ast
import dataclasses
import json
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Final

ROOT: Final[Path] = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.integration.real_odoo.pins import E2E_PINS  # noqa: E402

CHANGE: Final[Path] = ROOT / "openspec/changes/add-reproducible-odoo19-e2e-harness"
PLANNING_BASE_SHA: Final[str] = "0ff164636617c03a51277055af45cef009277368"
TASKS: Final[Path] = CHANGE / "tasks.md"
MATRIX: Final[Path] = CHANGE / "command-matrix.md"
EVIDENCE_ID = re.compile(r"E2E-(?:SM|CP|FC|REC|SEC)-\d{2}")
EVIDENCE_RANGE = re.compile(r"\b(E2E-(?:SM|CP|FC|REC|SEC)-)(\d{2})\.\.(\d{2})\b")
TASK_ID = re.compile(r"\[T(\d{2})\]")
SCENARIO = re.compile(r"^#### Scenario: (.+)$", re.MULTILINE)
REQUIRED_RUNS: Final[tuple[tuple[str, str], ...]] = (
    ("smoke", "cold"),
    ("smoke", "warm"),
    ("full", "cold"),
    ("full", "warm"),
)
SUCCESS_ARTIFACT_LIMIT_BYTES: Final[int] = 2 * 1024 * 1024
FAILURE_BUNDLE_LIMIT_BYTES: Final[int] = 50 * 1024 * 1024
WP03_HASH_LOCK_SOURCE_PATHS: Final[frozenset[str]] = frozenset(
    {
        "src/odoo_instance_sdk/commands/env.py",
        "src/odoo_instance_sdk/internal/dependency_sync.py",
        "src/odoo_instance_sdk/resources/environment.py",
    }
)

# This is deliberately a registry of executable selectors, rather than a
# search for evidence-id strings in source.  A selector is executable only if
# its test module and collected node exist; smoke points at the integrated
# upstream smoke implementation rather than inventing another scenario.
EVIDENCE_EXECUTORS: Final[dict[str, tuple[str, ...]]] = {
    **{
        f"E2E-SM-{number:02d}": (
            "tests/integration/real_odoo/test_smoke.py::test_container_smoke_public_path",
        )
        for number in range(1, 6)
    },
    **{
        f"E2E-CP-{number:02d}": (
            "tests/integration/real_odoo/test_critical_path.py::"
            "test_source_backed_full_critical_path",
        )
        for number in range(1, 16)
    },
    "E2E-FC-01": (
        "tests/integration/real_odoo/test_focused_failures.py::"
        "test_remote_auth_and_unreachable_source_fail_closed",
    ),
    "E2E-FC-02": (
        "tests/integration/real_odoo/test_focused_failures.py::"
        "test_remote_auth_and_unreachable_source_fail_closed",
    ),
    "E2E-FC-03": (
        "tests/integration/real_odoo/test_focused_failures.py::"
        "test_archive_and_restore_boundaries_publish_no_unowned_state[truncated]",
    ),
    "E2E-FC-04": (
        "tests/integration/real_odoo/test_focused_failures.py::"
        "test_archive_and_restore_boundaries_publish_no_unowned_state[incompatible]",
    ),
    "E2E-FC-05": (
        "tests/integration/real_odoo/test_focused_failures.py::"
        "test_catalog_restore_is_exact_and_occupied_or_repeated_targets_fail",
    ),
    "E2E-FC-06": (
        "tests/integration/real_odoo/test_focused_failures.py::"
        "test_remaining_focused_public_leaves_use_canonical_inventory[db.reset-admin-password]",
        "tests/integration/real_odoo/test_focused_failures.py::"
        "test_remaining_focused_public_leaves_use_canonical_inventory[shell]",
    ),
    "E2E-FC-07": (
        "tests/integration/real_odoo/test_focused_failures.py::"
        "test_remaining_focused_public_leaves_use_canonical_inventory[exec]",
    ),
    "E2E-FC-08": (
        "tests/integration/real_odoo/test_focused_failures.py::"
        "test_remaining_focused_public_leaves_use_canonical_inventory[module.test]",
    ),
    "E2E-FC-09": (
        "tests/integration/real_odoo/test_focused_failures.py::"
        "test_remaining_focused_public_leaves_use_canonical_inventory[backup.rm]",
    ),
    "E2E-FC-10": (
        "tests/integration/real_odoo/test_focused_failures.py::"
        "test_remaining_focused_public_leaves_use_canonical_inventory[db.rm]",
    ),
    "E2E-FC-11": (
        "tests/integration/real_odoo/test_focused_failures.py::"
        "test_remaining_focused_public_leaves_use_canonical_inventory[db.locks]",
        "tests/integration/real_odoo/test_focused_failures.py::"
        "test_remaining_focused_public_leaves_use_canonical_inventory[db.stats]",
        "tests/integration/real_odoo/test_focused_failures.py::"
        "test_remaining_focused_public_leaves_use_canonical_inventory[db.bloat]",
        "tests/integration/real_odoo/test_focused_failures.py::"
        "test_remaining_focused_public_leaves_use_canonical_inventory[db.init-monitoring]",
    ),
    "E2E-FC-12": (
        "tests/integration/real_odoo/test_focused_failures.py::"
        "test_remaining_focused_public_leaves_use_canonical_inventory[psql]",
    ),
    "E2E-FC-13": (
        "tests/integration/real_odoo/test_focused_failures.py::"
        "test_remaining_focused_public_leaves_use_canonical_inventory[logs]",
    ),
    **{
        f"E2E-REC-{number:02d}": (
            "tests/integration/real_odoo/test_focused_failures.py::"
            "test_sigint_timeout_and_partial_publication_recover_without_leaks",
        )
        for number in range(1, 4)
    },
    "E2E-SEC-01": (
        "tests/integration/real_odoo/test_focused_failures.py::"
        "test_remote_auth_and_unreachable_source_fail_closed",
    ),
    "E2E-SEC-02": (
        "tests/integration/real_odoo/test_focused_failures.py::"
        "test_remote_auth_and_unreachable_source_fail_closed",
    ),
    "E2E-SEC-03": (
        "tests/integration/real_odoo/test_focused_failures.py::"
        "test_failed_debug_retention_contains_only_sanitized_files",
    ),
    "E2E-SEC-04": (
        "tests/unit/test_real_odoo_ci.py::test_python_resolution_audit_rejects_unknown_scanner_finding",
    ),
    "E2E-SEC-05": (
        "tests/unit/test_real_odoo_ci.py::test_python_resolution_audit_rejects_missing_scanner_finding",
    ),
    "E2E-SEC-06": (
        "tests/unit/test_real_odoo_ci.py::test_full_python_resolution_audit_rejects_lock_or_report_drift",
    ),
    "E2E-SEC-07": (
        "tests/unit/test_dependency_sync.py::test_hash_lock_validation_requires_a_complete_immutable_pair",
    ),
}

SCENARIO_EVIDENCE: Final[dict[str, tuple[str, ...]]] = {
    "Full tier exercises product-owned target runtime": ("E2E-CP-01", "E2E-CP-02"),
    "Pinned input changes are explicit": ("E2E-CP-01", "E2E-CP-02"),
    "Backup contains database and filestore": ("E2E-CP-08", "E2E-CP-09"),
    "Fixture remains lightweight": ("E2E-CP-05", "E2E-CP-07"),
    "Concurrent runs do not share mutable state": ("E2E-SM-01",),
    "Readiness is semantic": ("E2E-CP-01",),
    "Public synchronization owns the trusted first install": ("E2E-CP-04", "E2E-SEC-07"),
    "Restored state is verified through Odoo": ("E2E-CP-10", "E2E-CP-11"),
    "Repeated public workflow is idempotent": ("E2E-CP-03", "E2E-CP-06", "E2E-CP-13"),
    "New CLI leaf cannot escape classification": ("E2E-CP-12",),
    "Matrix is generated from the canonical inventory": ("E2E-CP-12",),
    "Remote authentication fails safely": ("E2E-FC-01", "E2E-SEC-01"),
    "Truncated archive is rejected before publication": ("E2E-FC-03", "E2E-FC-04"),
    "Interrupted test leaves no owned resource": ("E2E-REC-01", "E2E-REC-02", "E2E-REC-03"),
    "Debug retention is explicit": ("E2E-REC-03", "E2E-SEC-03"),
    "Required E2E cannot pass by skipping": ("E2E-SM-05", "E2E-SEC-02"),
    "Budget classification is measurable": ("E2E-SEC-03",),
    "Failure artifact is useful and secret-free": ("E2E-SEC-01", "E2E-SEC-03"),
    "Unsupported platform fails explicitly": ("E2E-SEC-02",),
    "Pinned scanner failure stops before provisioning": ("E2E-SEC-04",),
    "Scanner and reviewed exceptions are exactly equal": ("E2E-SEC-04", "E2E-SEC-05"),
    "Exception metadata is valid and current": ("E2E-SEC-06",),
    "Approved scan permits provisioning": ("E2E-SEC-06",),
}

REQUIRED_TIER_EVIDENCE: Final[dict[str, tuple[str, ...]]] = {
    "smoke": tuple(f"E2E-SM-{number:02d}" for number in range(1, 6)),
    "full": tuple(
        f"E2E-{family}-{number:02d}"
        for family, maximum in (("CP", 15), ("FC", 13), ("REC", 3), ("SEC", 3))
        for number in range(1, maximum + 1)
    ),
}

SCENARIO_EXECUTORS: Final[dict[str, tuple[str, ...]]] = {
    name: tuple(
        dict.fromkeys(
            selector
            for identifier in identifiers
            for selector in EVIDENCE_EXECUTORS.get(identifier, ())
        )
    )
    for name, identifiers in SCENARIO_EVIDENCE.items()
}
SCENARIO_EXECUTORS.update(
    {
        "New CLI leaf cannot escape classification": (
            "tests/unit/test_real_odoo_contract.py::test_new_leaf_without_metadata_fails_closed",
            *SCENARIO_EXECUTORS["New CLI leaf cannot escape classification"],
        ),
        "Matrix is generated from the canonical inventory": (
            "tests/unit/test_real_odoo_contract.py::test_generated_matrix_matches_canonical_inventory",
            *SCENARIO_EXECUTORS["Matrix is generated from the canonical inventory"],
        ),
        "Budget classification is measurable": (
            "tests/unit/test_real_odoo_ci.py::test_evidence_exercises_smoke_budget_classification[warm]",
            *SCENARIO_EXECUTORS["Budget classification is measurable"],
        ),
    }
)


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _run(name: str, command: list[str], *, timeout: float = 900.0) -> dict[str, object]:
    started = time.monotonic()
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
        )
        return {
            "name": name,
            "command": command,
            "exit_code": result.returncode,
            "seconds": round(time.monotonic() - started, 6),
            "status": "passed" if result.returncode == 0 else "failed",
        }
    except (OSError, subprocess.SubprocessError) as error:
        return {
            "name": name,
            "command": command,
            "exit_code": None,
            "seconds": round(time.monotonic() - started, 6),
            "status": "failed",
            "error": type(error).__name__,
        }


def _matrix_evidence_ids(matrix_text: str) -> tuple[str, ...]:
    identifiers = set(EVIDENCE_ID.findall(matrix_text))
    for prefix, first, last in EVIDENCE_RANGE.findall(matrix_text):
        identifiers.update(f"{prefix}{number:02d}" for number in range(int(first), int(last) + 1))
    return tuple(sorted(identifiers))


def _collect_pytest_nodes(module_paths: Iterable[str]) -> frozenset[str]:
    existing_paths = tuple(path for path in module_paths if (ROOT / path).is_file())
    if not existing_paths:
        return frozenset()
    result = subprocess.run(
        [
            "uv",
            "run",
            "pytest",
            "-o",
            "addopts=",
            "--strict-markers",
            "--collect-only",
            "-q",
            *existing_paths,
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=300.0,
    )
    return frozenset(
        line.strip()
        for line in result.stdout.splitlines()
        if line.startswith("tests/") and "::" in line
    )


def _emitted_ids_for_selector(selector: str) -> frozenset[str]:
    path_text, separator, node_id = selector.partition("::")
    if not separator:
        return frozenset()
    function_name, _, parameter = node_id.partition("[")
    path = ROOT / path_text
    if not path.is_file():
        return frozenset()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeError):
        return frozenset()
    if (
        function_name == "test_remaining_focused_public_leaves_use_canonical_inventory"
        and parameter
    ):
        from tests.unit.test_cli_output_modes import PUBLIC_LEAF_CASES

        path_value = parameter.removesuffix("]")
        return frozenset(
            evidence
            for case in PUBLIC_LEAF_CASES
            if ".".join(case.path) == path_value
            for evidence in case.e2e_evidence
        )
    return frozenset(
        value
        for node in ast.walk(tree)
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == function_name
        for value_node in ast.walk(node)
        if isinstance(value_node, ast.Constant)
        and isinstance(value_node.value, str)
        and (value := value_node.value)
        and EVIDENCE_ID.fullmatch(value)
    )


def _validate_evidence_executors(
    evidence_ids: Iterable[str],
    executors: Mapping[str, tuple[str, ...]],
    collected_nodes: Iterable[str],
) -> tuple[str, ...]:
    collected = frozenset(collected_nodes)
    errors: list[str] = []
    for identifier in evidence_ids:
        selectors = executors.get(identifier, ())
        if not selectors:
            errors.append(f"{identifier}: missing executor selector")
            continue
        emitted: set[str] = set()
        for selector in selectors:
            if selector not in collected:
                errors.append(f"{identifier}: pytest node was not collected: {selector}")
            emitted.update(_emitted_ids_for_selector(selector))
        if identifier not in emitted:
            errors.append(f"{identifier}: collected executor does not emit this evidence")
    return tuple(sorted(errors))


def _validate_scenario_executors(
    scenario_names: Iterable[str],
    scenario_mapping: Mapping[str, tuple[str, ...]],
    scenario_executors: Mapping[str, tuple[str, ...]],
    collected_nodes: Iterable[str],
) -> tuple[str, ...]:
    collected = frozenset(collected_nodes)
    errors: list[str] = []
    for name in scenario_names:
        selectors = scenario_executors.get(name, ())
        if not selectors:
            errors.append(f"{name}: missing executable scenario selector")
            continue
        emitted: set[str] = set()
        for selector in selectors:
            if selector not in collected:
                errors.append(f"{name}: scenario node was not collected: {selector}")
            emitted.update(_emitted_ids_for_selector(selector))
        if not emitted.intersection(scenario_mapping.get(name, ())):
            errors.append(f"{name}: executable selectors emit no mapped evidence")
    return tuple(sorted(errors))


def _scenario_mapping_errors(
    scenario_names: Iterable[str], mapping: Mapping[str, tuple[str, ...]]
) -> tuple[str, ...]:
    names = tuple(scenario_names)
    errors: list[str] = []
    if len(names) != len(set(names)):
        errors.append("duplicate scenario heading")
    missing = sorted(set(names) - set(mapping))
    stale = sorted(set(mapping) - set(names))
    errors.extend(f"missing scenario mapping: {name}" for name in missing)
    errors.extend(f"stale scenario mapping: {name}" for name in stale)
    for name, identifiers in mapping.items():
        if not identifiers:
            errors.append(f"empty scenario mapping: {name}")
        if len(identifiers) != len(set(identifiers)):
            errors.append(f"duplicate evidence mapping: {name}")
    return tuple(sorted(errors))


def _scope_contract() -> dict[str, object]:
    tracked = subprocess.run(
        ["git", "diff", "--name-only", "origin/main...HEAD"],
        cwd=ROOT,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.splitlines()
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.splitlines()
    changed = sorted(set(tracked) | set(untracked))
    forbidden = [
        path
        for path in changed
        if (path.startswith("src/") and path not in WP03_HASH_LOCK_SOURCE_PATHS)
        or path.endswith((".zip", ".dump"))
        or "enterprise" in path.casefold()
    ]
    return {
        "base": PLANNING_BASE_SHA,
        "scope_base": "origin/main...HEAD",
        "changed_files": changed,
        "forbidden_changed_files": forbidden,
        "out_of_scope": not forbidden,
    }


def _junit_evidence_ids(root: ET.Element) -> frozenset[str]:
    return frozenset(
        normalized
        for property_node in root.findall(".//property")
        for name in [property_node.get("name", "")]
        for normalized in [name.upper().replace("_", "-")]
        if EVIDENCE_ID.fullmatch(normalized)
    )


def _static_contract() -> dict[str, object]:
    from tests.integration.real_odoo.contracts import check_matrix_document, validate_leaf_metadata
    from tests.unit.test_cli_output_modes import PUBLIC_LEAF_CASES

    validate_leaf_metadata(PUBLIC_LEAF_CASES)
    check_matrix_document(str(MATRIX), PUBLIC_LEAF_CASES)
    matrix_text = MATRIX.read_text(encoding="utf-8")
    task_text = TASKS.read_text(encoding="utf-8")
    task_ids = [f"T{value}" for value in TASK_ID.findall(task_text)]
    expected_tasks = [f"T{number:02d}" for number in range(1, 27)]
    evidence_ids = _matrix_evidence_ids(matrix_text)
    scenario_names = SCENARIO.findall(
        (CHANGE / "specs/real-odoo-e2e-verification/spec.md").read_text(encoding="utf-8")
    )
    scenario_mapping_errors = _scenario_mapping_errors(scenario_names, SCENARIO_EVIDENCE)
    collected_nodes = _collect_pytest_nodes(
        sorted(
            {
                selector.split("::", 1)[0]
                for selectors in EVIDENCE_EXECUTORS.values()
                for selector in selectors
            }
            | {
                selector.split("::", 1)[0]
                for selectors in SCENARIO_EXECUTORS.values()
                for selector in selectors
            }
        )
    )
    executor_errors = _validate_evidence_executors(
        evidence_ids, EVIDENCE_EXECUTORS, collected_nodes
    )
    executor_error_ids = {error.split(":", 1)[0] for error in executor_errors}
    executable = sorted(set(evidence_ids) - executor_error_ids)
    missing_evidence = sorted(executor_error_ids)
    scenario_executor_errors = _validate_scenario_executors(
        scenario_names, SCENARIO_EVIDENCE, SCENARIO_EXECUTORS, collected_nodes
    )
    scenario_mapping_errors = tuple(sorted((*scenario_mapping_errors, *scenario_executor_errors)))
    scenario_missing_evidence = sorted(
        {
            identifier
            for identifiers in SCENARIO_EVIDENCE.values()
            for identifier in identifiers
            if identifier in executor_error_ids
        }
    )
    scope = _scope_contract()
    return {
        "tasks": {
            "expected": expected_tasks,
            "observed": task_ids,
            "exactly_once": task_ids == expected_tasks,
            "incomplete": [line for line in task_text.splitlines() if line.startswith("- [ ]")],
        },
        "inventory": {
            "rows": len(PUBLIC_LEAF_CASES),
            "unique_paths": len({case.path for case in PUBLIC_LEAF_CASES}),
            "matrix_matches": True,
        },
        "evidence": {
            "matrix_ids": evidence_ids,
            "executable_ids": executable,
            "missing_ids": missing_evidence,
            "executor_errors": executor_errors,
            "collected_nodes": sorted(collected_nodes),
            "smoke_marker_present": bool(
                any(identifier.startswith("E2E-SM-") for identifier in executable)
            ),
            "scenario_count": len(scenario_names),
            "scenario_names": scenario_names,
            "scenario_mapping": SCENARIO_EVIDENCE,
            "scenario_mapping_valid": not scenario_mapping_errors,
            "scenario_mapping_errors": scenario_mapping_errors,
            "scenario_missing_evidence": scenario_missing_evidence,
        },
        "scope": {
            **scope,
        },
    }


def _json_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path.name} must be a JSON object")
    return value


def _run_evidence_state(path: Path, tier: str, cache_class: str) -> dict[str, object]:  # noqa: C901
    """Validate one completed tier/cache evidence bundle fail-closed."""
    if not path.is_dir():
        return {"status": "missing", "reason": "run directory is absent"}
    bootstrap_path = path / "bootstrap.json"
    if bootstrap_path.is_file():
        try:
            bootstrap = _json_object(bootstrap_path)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            return {"status": "failed", "reason": f"invalid bootstrap: {error}"}
        if bootstrap.get("status") == "not-run":
            return {"status": "missing", "reason": "bootstrap was not run"}
        if bootstrap.get("ok") is not True:
            return {
                "status": "blocked",
                "reason": "bootstrap did not satisfy prerequisites",
                "missing": bootstrap.get("missing", []),
            }
        if bootstrap.get("pins") != dataclasses.asdict(E2E_PINS):
            return {"status": "failed", "reason": "bootstrap pin manifest is stale or incomplete"}
        if tier == "full":
            prerequisites = bootstrap.get("prerequisites")
            if not isinstance(prerequisites, dict) or any(
                prerequisites.get(name) is not True
                for name in (
                    "python_resolution_lock",
                    "python_resolution_audit",
                    "odoo_source_revision",
                )
            ):
                return {
                    "status": "failed",
                    "reason": "full bootstrap lacks a successful audited source prerequisite",
                }
    required = (
        "bootstrap.json",
        "junit.xml",
        "timing.json",
        "resource-manifest.json",
        "evidence-manifest.json",
    )
    missing = [name for name in required if not (path / name).is_file()]
    if missing:
        return {"status": "missing", "missing": missing}
    try:
        bootstrap = _json_object(path / "bootstrap.json")
        cache = bootstrap.get("cache")
        if not isinstance(cache, dict) or cache.get("class") != cache_class:
            return {"status": "failed", "reason": "bootstrap cache class is incomplete"}

        junit_root = ET.parse(path / "junit.xml").getroot()
        suites = (
            [junit_root]
            if junit_root.tag == "testsuite"
            else list(junit_root.findall(".//testsuite"))
        )
        tests = sum(int(suite.get("tests", "0")) for suite in suites)
        failures = sum(
            int(suite.get("failures", "0")) + int(suite.get("errors", "0")) for suite in suites
        )
        if tests <= 0 or failures:
            return {"status": "failed", "reason": "JUnit is incomplete or failed"}
        emitted = _junit_evidence_ids(junit_root)
        missing_emitted = sorted(set(REQUIRED_TIER_EVIDENCE[tier]) - set(emitted))
        if missing_emitted:
            return {
                "status": "failed",
                "reason": "JUnit is missing emitted evidence IDs",
                "missing_evidence": missing_emitted,
            }

        timing = _json_object(path / "timing.json")
        phases = timing.get("phases")
        if not isinstance(phases, dict) or any(
            not isinstance(phases.get(phase), dict)
            or not isinstance(phases[phase].get("duration_seconds"), (int, float))
            or phases[phase]["duration_seconds"] < 0
            for phase in ("setup", "test", "cleanup")
        ):
            return {"status": "failed", "reason": "timing phases are incomplete"}
        budget = timing.get("budget")
        if (
            not isinstance(budget, dict)
            or budget.get("ok") is not True
            or budget.get("artifact_budget_bytes") != SUCCESS_ARTIFACT_LIMIT_BYTES
        ):
            return {"status": "failed", "reason": "timing budget result is missing or failed"}

        evidence = _json_object(path / "evidence-manifest.json")
        if evidence.get("ok") is not True:
            return {"status": "failed", "reason": "bundle result is not successful"}
        artifact_bytes = evidence.get("artifact_bytes")
        if not isinstance(artifact_bytes, int) or artifact_bytes < 0:
            return {"status": "failed", "reason": "bundle size is missing"}
        if artifact_bytes > SUCCESS_ARTIFACT_LIMIT_BYTES:
            return {"status": "failed", "reason": "bundle exceeds success cap"}
        if (
            evidence.get("bundle_limit_bytes") != FAILURE_BUNDLE_LIMIT_BYTES
            or evidence.get("success_limit_bytes") != SUCCESS_ARTIFACT_LIMIT_BYTES
        ):
            return {"status": "failed", "reason": "bundle cap result is missing"}
        evidence_budget = evidence.get("budget")
        if (
            not isinstance(evidence_budget, dict)
            or evidence_budget.get("ok") is not True
            or evidence_budget.get("artifact_bytes") != artifact_bytes
        ):
            return {"status": "failed", "reason": "bundle budget result is missing or failed"}

        resource = _json_object(path / "resource-manifest.json")
        audit = resource.get("audit")
        if (
            not isinstance(audit, dict)
            or audit.get("state") != "clean"
            or audit.get("leaks") != []
            or not isinstance(audit.get("runs"), list)
            or not audit["runs"]
        ):
            return {"status": "failed", "reason": "completed leak audit is not empty"}
        if tier == "full" and resource.get("source_cache_consumed") is not True:
            return {"status": "failed", "reason": "full source-cache audit is incomplete"}
    except (OSError, ValueError, TypeError, ET.ParseError, json.JSONDecodeError) as error:
        return {"status": "failed", "reason": f"invalid evidence: {error}"}
    return {
        "status": "complete",
        "tier": tier,
        "cache_class": cache_class,
        "tests": tests,
        "artifact_bytes": artifact_bytes,
    }


def _e2e_bootstrap_state(root: Path) -> dict[str, object]:
    runs = {
        f"{tier}-{cache_class}": _run_evidence_state(
            root / f"{tier}-{cache_class}", tier, cache_class
        )
        for tier, cache_class in REQUIRED_RUNS
    }
    statuses = {str(value["status"]) for value in runs.values() if isinstance(value, dict)}
    status = (
        "complete"
        if statuses == {"complete"}
        else "failed"
        if "failed" in statuses
        else "blocked"
        if "blocked" in statuses
        else "missing"
    )
    return {"status": status, "runs": runs}


def _write_acceptance_artifacts(
    root: Path,
    *,
    static: dict[str, object],
    e2e: dict[str, object],
    ledger: list[dict[str, object]],
) -> None:
    e2e_status = e2e.get("status")
    complete = e2e_status == "complete"
    setup_seconds = 0.0
    for item in ledger:
        duration = item.get("seconds")
        if isinstance(duration, (int, float)):
            setup_seconds += duration
    setup_seconds = round(setup_seconds, 6)
    _write(
        root / "timing.json",
        {
            "schema": "odcli-real-odoo-acceptance-timing-v1",
            "status": "ready" if complete else "blocked",
            "phases": {
                "setup": {"duration_seconds": setup_seconds},
                "test": {"duration_seconds": None},
                "cleanup": {"duration_seconds": 0.0},
            },
        },
    )
    _write(
        root / "cleanup-audit.json",
        {
            "state": "clean" if complete else "blocked",
            "leaks": [],
            "reason": "all required E2E runs supplied completed empty audits"
            if complete
            else "required E2E evidence is missing, blocked, or failed",
        },
    )
    suite = ET.Element("testsuite", name="real-odoo-static-acceptance", tests=str(len(ledger)))
    suite.set("failures", str(sum(item["status"] != "passed" for item in ledger)))
    for item in ledger:
        testcase = ET.SubElement(suite, "testcase", name=str(item["name"]))
        if item["status"] != "passed":
            ET.SubElement(testcase, "failure", message="command failed")
    ET.ElementTree(suite).write(root / "junit.xml", encoding="utf-8", xml_declaration=True)
    (root / "junit.xml").chmod(0o600)
    evidence = static["evidence"]
    scope = static["scope"]
    static_ok = (
        isinstance(evidence, dict)
        and not evidence["missing_ids"]
        and evidence["smoke_marker_present"] is True
        and evidence["scenario_mapping_valid"] is True
        and not evidence["scenario_missing_evidence"]
        and isinstance(scope, dict)
        and scope["out_of_scope"] is True
    )
    report = (
        "## Real-Odoo WP acceptance evidence\n\n"
        f"Static gates: {'PASS' if static_ok else 'FAIL'}\n\n"
        f"E2E bootstrap: {'READY' if complete else 'BLOCKED'}\n\n"
        "The local run records prerequisite state and does not convert missing "
        "prerequisites into pytest skips.\n"
    )
    (root / "acceptance-report.md").write_text(report, encoding="utf-8")
    (root / "acceptance-report.md").chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", type=Path, default=Path(".artifacts/real-odoo-e2e"))
    args = parser.parse_args()
    artifact_root = (
        args.artifact_root if args.artifact_root.is_absolute() else ROOT / args.artifact_root
    )
    static = _static_contract()
    ledger = [
        _run("format", ["uv", "run", "ruff", "format", "--check", "."]),
        _run("lint", ["uv", "run", "ruff", "check", "."]),
        _run("type-sdk", ["uv", "run", "mypy", "--strict", "src/odoo_instance_sdk"]),
        _run(
            "type-tests-scripts",
            [
                "uv",
                "run",
                "mypy",
                "tests",
                "scripts",
                "--namespace-packages",
                "--explicit-package-bases",
                "--ignore-missing-imports",
                "--follow-imports=silent",
                "--check-untyped-defs",
            ],
        ),
        _run("inventory-and-contract", ["uv", "run", "python", "scripts/check_e2e_contract.py"]),
        _run(
            "unit-contracts",
            [
                "uv",
                "run",
                "pytest",
                "-o",
                "addopts=",
                "--strict-markers",
                "-q",
                "tests/unit/test_real_odoo_contract.py",
                "tests/unit/test_real_odoo_ci.py",
                "tests/unit/test_real_odoo_ci_components.py",
                "tests/unit/test_real_odoo_foundation.py",
                "tests/unit/test_real_odoo_failures.py",
            ],
            timeout=300.0,
        ),
        _run(
            "openspec-strict",
            [
                "openspec",
                "validate",
                "--changes",
                "--strict",
                "--no-interactive",
            ],
        ),
    ]
    _write(artifact_root / "pin-manifest.json", dataclasses.asdict(E2E_PINS))
    (artifact_root / "command-matrix.md").parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    (artifact_root / "command-matrix.md").write_text(
        MATRIX.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (artifact_root / "command-matrix.md").chmod(0o600)
    _write(artifact_root / "command-ledger.json", {"commands": ledger})
    _write(artifact_root / "out-of-scope.json", static["scope"])
    e2e = _e2e_bootstrap_state(artifact_root)
    _write_acceptance_artifacts(artifact_root, static=static, e2e=e2e, ledger=ledger)
    _write(artifact_root / "acceptance.json", {"static": static, "e2e": e2e})
    print(json.dumps({"static": static, "e2e": e2e}, sort_keys=True))
    evidence = static["evidence"]
    scope = static["scope"]
    static_ok = (
        isinstance(evidence, dict)
        and isinstance(scope, dict)
        and not evidence["missing_ids"]
        and evidence["smoke_marker_present"] is True
        and evidence["scenario_mapping_valid"] is True
        and not evidence["scenario_missing_evidence"]
        and scope["out_of_scope"] is True
    )
    return (
        0
        if static_ok
        and e2e.get("status") == "complete"
        and all(item["status"] == "passed" for item in ledger)
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
