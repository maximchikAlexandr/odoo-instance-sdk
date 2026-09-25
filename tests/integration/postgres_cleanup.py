from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from odoo_instance_sdk.exceptions import PostgresImageNotTrustedError
from odoo_instance_sdk.internal.dbprep.bootstrap import BootstrapTmpSteps
from odoo_instance_sdk.internal.proc import PreparedStep, RunContext
from odoo_instance_sdk.resources.postgres import PostgresCluster

if TYPE_CHECKING:
    from _pytest.monkeypatch import MonkeyPatch

_EXPLICITLY_ABSENT_VOLUME = re.compile(
    r"^Error response from daemon: get [^:]+: no such volume$", re.IGNORECASE
)


def _run_compose_cleanup(compose_file: Path, compose_project_name: str) -> None:
    cleanup = subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            compose_project_name,
            "-f",
            str(compose_file),
            "down",
            "--volumes",
            "--remove-orphans",
        ],
        cwd=compose_file.parent,
        capture_output=True,
        check=False,
        timeout=60.0,
        text=True,
    )
    if cleanup.returncode != 0:
        raise AssertionError(
            f"compose cleanup failed for {compose_project_name}: {cleanup.stderr.strip()}"
        )


def _run_volume_inspect(volume_name: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "volume", "inspect", volume_name],
        capture_output=True,
        check=False,
        timeout=60.0,
        text=True,
    )


def _validate_present_volume(volume_name: str, stdout: str) -> None:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"docker volume inspect returned malformed output for {volume_name}"
        ) from exc
    if (
        not isinstance(payload, list)
        or len(payload) != 1
        or not isinstance(payload[0], dict)
        or payload[0].get("Name") != volume_name
    ):
        raise AssertionError(f"docker volume inspect returned malformed output for {volume_name}")


def _assert_volume_absent(volume_name: str) -> None:
    volume = _run_volume_inspect(volume_name)
    if volume.returncode == 0:
        _validate_present_volume(volume_name, volume.stdout)
        raise AssertionError(f"disposable volume remains after cleanup: {volume_name}")
    stdout = volume.stdout.strip()
    empty_inspection = not stdout
    if stdout:
        try:
            empty_inspection = json.loads(stdout) == []
        except json.JSONDecodeError:
            empty_inspection = False
    if (
        volume.returncode == 1
        and empty_inspection
        and _EXPLICITLY_ABSENT_VOLUME.fullmatch(volume.stderr.strip())
    ):
        return
    detail = volume.stderr.strip() or volume.stdout.strip() or "no diagnostic output"
    raise AssertionError(f"docker volume inspect probe failed for {volume_name}: {detail}")


def cleanup_postgres_project(
    *,
    compose_file: Path,
    compose_project_name: str,
    volume_name: str,
    primary_failure: BaseException | None,
) -> None:
    """Tear down one disposable Compose project without hiding failures."""
    cleanup_failures: list[BaseException] = []
    if compose_file.is_file():
        try:
            _run_compose_cleanup(compose_file, compose_project_name)
        except BaseException as exc:
            cleanup_failures.append(exc)

    try:
        _assert_volume_absent(volume_name)
    except BaseException as exc:
        cleanup_failures.append(exc)

    if primary_failure is not None and cleanup_failures:
        raise BaseExceptionGroup(
            "primary test failure and PostgreSQL cleanup failures",
            [primary_failure, *cleanup_failures],
        )
    if primary_failure is not None:
        raise primary_failure
    if cleanup_failures:
        raise BaseExceptionGroup("PostgreSQL cleanup failures", cleanup_failures)


def patch_postgres_image_trust(monkeypatch: MonkeyPatch) -> None:
    """Auto-approve the resolved compose image digest for integration tests."""
    original = PostgresCluster._approved_image_digest

    def _approved_image_digest(self: PostgresCluster) -> str:
        try:
            return original(self)
        except PostgresImageNotTrustedError:
            image = self._image
            assert image is not None
            pull = subprocess.run(
                ["docker", "image", "pull", image],
                capture_output=True,
                text=True,
                check=False,
            )
            if pull.returncode != 0:
                raise PostgresImageNotTrustedError(
                    f"docker image pull failed: {pull.stderr.strip() or pull.stdout.strip()}"
                ) from None
            inspected = subprocess.run(
                [
                    "docker",
                    "image",
                    "inspect",
                    "--format",
                    "{{index .RepoDigests 0}}",
                    image,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if inspected.returncode != 0:
                raise PostgresImageNotTrustedError(
                    "docker image inspect failed: "
                    f"{inspected.stderr.strip() or inspected.stdout.strip()}"
                ) from None
            digest = inspected.stdout.strip()
            self._approve_image(digest)
            return digest

    monkeypatch.setattr(PostgresCluster, "_approved_image_digest", _approved_image_digest)


def patch_compose_init_bootstrap_skip(monkeypatch: MonkeyPatch) -> None:
    """Skip tmp bootstrap during partial compose init in integration tests."""

    def _skip_bootstrap_tmp(
        context: RunContext[object],
        spawn_step: PreparedStep,
        probe_step: PreparedStep,
        ready_step: PreparedStep,
    ) -> bool:
        context.skip(spawn_step.step_id)
        context.skip(probe_step.step_id)
        context.skip(ready_step.step_id)
        return True

    def _skip_project_bootstrap_tmp(
        _instance: object,
        context: RunContext[object],
        *,
        steps: BootstrapTmpSteps | tuple[()] | None = None,
    ) -> None:
        for step in steps or ():
            context.skip(step.step_id)

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.dbprep.bootstrap.run_bootstrap_tmp",
        _skip_bootstrap_tmp,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.dbprep.bootstrap.ensure_project_bootstrap_tmp",
        _skip_project_bootstrap_tmp,
    )


def patch_compose_init_for_integration(monkeypatch: MonkeyPatch) -> None:
    """Prepare partial compose init for disposable PostgreSQL integration tests."""
    patch_postgres_image_trust(monkeypatch)
    patch_compose_init_bootstrap_skip(monkeypatch)
