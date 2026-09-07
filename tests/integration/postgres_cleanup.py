from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

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
