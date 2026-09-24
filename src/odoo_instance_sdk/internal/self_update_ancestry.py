"""Captured Git ancestry probes for the self-update preflight."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Literal, cast
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from odoo_instance_sdk.internal.proc import (
    PreparedStep,
    ProcessResult,
    RunContext,
    SubprocessExecutor,
)
from odoo_instance_sdk.models.update import UpdateResult

RevisionRelation = Literal["same", "descendant", "ancestor", "divergent", "unknown"]


def revision_probe_steps(
    *, source_repo: str | None, installed_sha: str, target_sha: str
) -> tuple[PreparedStep, ...]:
    """Capture the Git ancestry probe without launching it during construction."""
    if installed_sha == target_sha or not source_repo:
        return ()
    repository = source_repo.removeprefix("git+")
    if repository.startswith("file://"):
        repository = unquote(urlsplit(repository).path)
    root = Path(tempfile.gettempdir()) / f"odcli-update-ancestry-{uuid4().hex}"
    git_dir = str(root / "objects.git")
    return (
        PreparedStep(
            step_id="update.inspect.ancestry-init",
            argv=("git", "init", "--bare", git_dir),
            mutating=True,
            timeout=30.0,
        ),
        PreparedStep(
            step_id="update.inspect.ancestry-fetch",
            argv=(
                "git",
                "--git-dir",
                git_dir,
                "fetch",
                "--no-tags",
                repository,
                installed_sha,
                target_sha,
            ),
            mutating=True,
            timeout=60.0,
        ),
        PreparedStep(
            step_id="update.inspect.ancestry-installed",
            argv=(
                "git",
                "--git-dir",
                git_dir,
                "merge-base",
                "--is-ancestor",
                installed_sha,
                target_sha,
            ),
            read_only=True,
            timeout=30.0,
        ),
        PreparedStep(
            step_id="update.inspect.ancestry-target",
            argv=(
                "git",
                "--git-dir",
                git_dir,
                "merge-base",
                "--is-ancestor",
                target_sha,
                installed_sha,
            ),
            read_only=True,
            timeout=30.0,
        ),
    )


def revision_relation_from_results(
    *,
    installed_sha: str,
    target_sha: str,
    installed_check: ProcessResult,
    target_check: ProcessResult,
) -> RevisionRelation:
    if installed_sha == target_sha:
        return "same"
    if installed_check.returncode == 0:
        return "descendant"
    if installed_check.returncode != 1:
        return "unknown"
    if target_check.returncode == 0:
        return "ancestor"
    return "divergent" if target_check.returncode == 1 else "unknown"


def git_revision_relation(
    *, source_repo: str | None, installed_sha: str, target_sha: str
) -> RevisionRelation:
    """Classify two revisions through the shared executor for direct callers/tests."""
    steps = revision_probe_steps(
        source_repo=source_repo, installed_sha=installed_sha, target_sha=target_sha
    )
    if installed_sha == target_sha:
        return "same"
    if not steps:
        return "unknown"
    executor = SubprocessExecutor()
    root = Path(steps[0].argv[3]).parent
    try:
        if executor.execute(steps[0]).returncode != 0 or executor.execute(steps[1]).returncode != 0:
            return "unknown"
        return revision_relation_from_results(
            installed_sha=installed_sha,
            target_sha=target_sha,
            installed_check=executor.execute(steps[2]),
            target_check=executor.execute(steps[3]),
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)


def run_revision_probe(
    context: RunContext[UpdateResult],
    steps: tuple[PreparedStep, ...],
    *,
    installed_sha: str,
    target_sha: str,
    cleanup_step_id: str | None = None,
) -> RevisionRelation:
    if installed_sha == target_sha:
        return "same"
    if not steps:
        return "unknown"
    root = Path(steps[0].argv[3]).parent
    try:
        init_result = cast("ProcessResult", context.process_prepared(steps[0]))
        if init_result.returncode != 0:
            for step in steps[1:]:
                context.skip(step.step_id)
            return "unknown"
        fetch_result = cast("ProcessResult", context.process_prepared(steps[1]))
        if fetch_result.returncode != 0:
            for step in steps[2:]:
                context.skip(step.step_id)
            return "unknown"
        return revision_relation_from_results(
            installed_sha=installed_sha,
            target_sha=target_sha,
            installed_check=cast("ProcessResult", context.process_prepared(steps[2])),
            target_check=cast("ProcessResult", context.process_prepared(steps[3])),
        )
    finally:
        if cleanup_step_id is None:
            shutil.rmtree(root, ignore_errors=True)
        else:
            context.action(cleanup_step_id)
            try:
                shutil.rmtree(root)
            except OSError as exc:
                context.fail_action(cleanup_step_id, exc)
                raise
            context.complete_action(cleanup_step_id)


__all__ = [
    "RevisionRelation",
    "git_revision_relation",
    "revision_probe_steps",
    "revision_relation_from_results",
    "run_revision_probe",
]
