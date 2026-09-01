from __future__ import annotations

import json
import multiprocessing as mp
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

import pytest

from odoo_instance_sdk.exceptions import PgAdminUnavailableError, StalePlanError
from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.internal import pgadmin, pgadmin_container, pgadmin_files
from odoo_instance_sdk.internal.address import AddressState
from odoo_instance_sdk.internal.proc import (
    PreparedStep,
    ProcessHandle,
    ProcessResult,
    ProcessSpawnError,
    RecordingExecutor,
    SubprocessExecutor,
    active_context,
)
from odoo_instance_sdk.models import PgAdminOpenResult, PgAdminOpenState

from .pgadmin_test_support import _paths

_LINUX_PHASE_GAP_ACL_STEPS = (
    "pgadmin.reconciliation.preparation.acl.root",
    "pgadmin.reconciliation.preparation.acl.private",
    "pgadmin.reconciliation.preparation.acl.data",
    "pgadmin.reconciliation.preparation.acl.data.default",
    "pgadmin.reconciliation.preparation.acl.fingerprint-key",
    "pgadmin.reconciliation.preparation.acl.admin-password",
    "pgadmin.reconciliation.preparation.acl.pgpass",
    "pgadmin.reconciliation.preparation.acl.servers",
    "pgadmin.reconciliation.preparation.acl.metadata",
)


def _run_phase_gap_command_in_fork(
    command: Command[PgAdminOpenResult], executor: RecordingExecutor, output: Any
) -> None:
    try:
        command.run()
    except BaseException as exc:
        output.put(
            (
                type(exc).__name__,
                str(exc),
                tuple(step.step_id for step in executor.executed),
            )
        )
    else:
        output.put(("success", "", tuple(step.step_id for step in executor.executed)))


def _linux_phase_gap_command(  # noqa: C901
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    witness: str,
) -> tuple[Command[PgAdminOpenResult], RecordingExecutor, tuple[str, ...], list[object]]:
    """Build a complete captured continuation with one Linux witness fault."""
    from odoo_instance_sdk.internal.postgres_compose import ComposeRunner
    from odoo_instance_sdk.internal.proc import PreparedAction, PreparedProcess

    paths = _paths(tmp_path)
    monkeypatch.setattr(pgadmin_files, "get_data_root", lambda *, ensure_exists: tmp_path)
    monkeypatch.setattr(pgadmin_files, "_linux", lambda: True)
    paths.root.mkdir(parents=True)
    paths.private_dir.mkdir()
    paths.data_dir.mkdir()
    paths.root.chmod(0o710)
    paths.private_dir.chmod(0o710)
    paths.data_dir.chmod(0o770)
    (paths.private_dir / ".fingerprint-key").write_bytes(b"k" * 32)
    (paths.private_dir / ".fingerprint-key").chmod(0o600)
    for path in (paths.admin_password, paths.pgpass, paths.servers_json, paths.metadata):
        path.write_bytes(b"captured\n")
        path.chmod(0o640)

    mounts = pgadmin_files.pgadmin_mounts(paths)
    if witness == "mount.destination":
        mounts = (
            pgadmin_files.PgAdminMount(
                paths.admin_password,
                "/var/lib/pgadmin/private/admin-password",
                True,
            ),
            *mounts[1:],
        )
    elif witness == "mount.read-only":
        mounts = (
            pgadmin_files.PgAdminMount(
                paths.admin_password,
                pgadmin_files.PGADMIN_PASSWORD_DESTINATION,
                False,
            ),
            *mounts[1:],
        )
    preparation = pgadmin_files.PgAdminPreparation(
        paths=paths,
        fingerprint="captured-hmac",
        port=5050,
        container_name=pgadmin_files.PGADMIN_CONTAINER_NAME,
        mounts=mounts,
    )
    prefix = pgadmin_files.preparation_revalidation_steps(paths)

    def acl_output(step: PreparedStep) -> str:
        if step.step_id == witness:
            if step.step_id.endswith(".default"):
                return "default:user::---"
            return "user::---"
        if step.step_id.endswith(".root") or step.step_id.endswith(".private"):
            entries = pgadmin_files._directory_acl(0o710)
        elif step.step_id.endswith(".data"):
            entries = pgadmin_files._directory_acl(0o770)
        elif step.step_id.endswith(".data.default"):
            entries = pgadmin_files._default_directory_acl()
            return "\n".join(f"default:{entry}" for entry in sorted(entries))
        elif step.step_id.endswith(".fingerprint-key"):
            entries = pgadmin_files._fingerprint_key_acl()
        else:
            entries = pgadmin_files._file_acl()
        return "\n".join(sorted(entries))

    def result_factory(process: PreparedProcess) -> ProcessResult:
        step = cast("PreparedStep", process)
        assert step.argv[:2] == ("getfacl", "-cp")
        assert step.read_only
        assert not step.mutating
        return ProcessResult(
            step.argv,
            0,
            acl_output(step),
            "",
            0.0,
            step.cwd,
            step.environment,
        )

    executor = RecordingExecutor(result_factory=result_factory)
    inspect_calls: list[object] = []

    def unexpected_inspect(*args: object, **kwargs: object) -> None:
        inspect_calls.append((args, kwargs))
        raise AssertionError("Docker inspect must not follow a failed preparation witness")

    monkeypatch.setattr(pgadmin_container, "inspect_container", unexpected_inspect)
    steps: tuple[PreparedStep | PreparedAction, ...] = (
        *prefix,
        pgadmin_container.reconciliation_inspect_step(),
        PreparedAction(
            step_id="pgadmin.reconciliation.port.revalidate",
            action="pgadmin_reconciliation_port_revalidate",
            read_only=True,
        ),
        *pgadmin_container.reconciliation_steps(
            paths=paths,
            port=5050,
            network="project_default",
            fingerprint="captured-hmac",
            secret_values=("captured-hmac", "database-password"),
        ),
    )
    carrier = pgadmin._PgAdminReconciliationCarrier(
        preparation=preparation,
        runner=cast("ComposeRunner", object()),
        network="project_default",
        database="demo",
        timeout=1.0,
        steps=steps,
        executor=executor,
        fingerprint_key=b"k" * 32,
    )
    command = carrier.reconciliation_command()
    return command, executor, tuple(step.step_id for step in prefix), inspect_calls


@pytest.mark.parametrize(
    "witness",
    (*_LINUX_PHASE_GAP_ACL_STEPS, "mount.destination", "mount.read-only"),
)
def test_linux_phase_gap_witness_is_read_only_and_matches_in_fork(
    witness: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every ACL/mount witness fails before Docker and preserves the exact prefix."""
    command, executor, prefix_ids, inspect_calls = _linux_phase_gap_command(
        tmp_path, monkeypatch, witness
    )
    planned_ids = tuple(step.step_id for step in command.plan.steps)
    assert planned_ids[: len(prefix_ids)] == prefix_ids
    if witness in _LINUX_PHASE_GAP_ACL_STEPS:
        expected_ids = prefix_ids[: prefix_ids.index(witness) + 1]
    else:
        expected_ids = ()

    context = mp.get_context("fork")
    output = context.Queue()
    child = context.Process(
        target=_run_phase_gap_command_in_fork,
        args=(command, executor, output),
    )
    child.start()
    child.join(10)
    assert child.exitcode == 0
    child_result = output.get(timeout=5)
    assert child_result[0] == "StalePlanError"
    assert child_result[1] == "captured pgAdmin preparation changed before execution"
    assert child_result[2] == expected_ids

    with pytest.raises(StalePlanError, match="preparation changed"):
        command.run()
    assert tuple(step.step_id for step in executor.executed) == expected_ids
    assert not inspect_calls
    assert all(step.argv[:2] == ("getfacl", "-cp") for step in executor.executed)
    assert all(step.read_only and not step.mutating for step in executor.executed)


def test_fingerprint_key_drift_fails_before_file_or_docker_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    monkeypatch.setattr(pgadmin_files, "get_data_root", lambda *, ensure_exists: tmp_path)
    paths.private_dir.mkdir(parents=True)
    (paths.private_dir / ".fingerprint-key").write_bytes(b"a" * 32)
    identity = pgadmin_files.PostgresIdentity(
        container_name="postgres-1",
        network="project_default",
        user="odoo",
        host="postgres-1",
    )
    cluster = type(
        "Cluster",
        (),
        {
            "compose_runner": object(),
            "compose_file": tmp_path / "compose.yaml",
            "compose_project_name": "project",
            "_user": "odoo",
        },
    )()
    instance = type("Instance", (), {"config": type("Config", (), {"db_password": "secret"})()})()
    current = {
        "Config": {
            "Labels": {
                pgadmin_files.PGADMIN_LABEL_MANAGED: "true",
                pgadmin_files.PGADMIN_LABEL_FINGERPRINT: "different-fingerprint",
                pgadmin_files.PGADMIN_LABEL_NETWORK: identity.network,
            }
        }
    }
    monkeypatch.setattr(
        pgadmin_container,
        "resolve_postgres_identity",
        lambda *_args, **_kwargs: identity,
    )
    inspect_calls: list[object] = []

    def inspect(*args: object, **kwargs: object) -> dict[str, object]:
        inspect_calls.append((args, kwargs))
        return cast("dict[str, object]", current)

    monkeypatch.setattr(pgadmin_container, "inspect_container", inspect)
    matches_calls: list[object] = []

    def matches(*args: object, **kwargs: object) -> bool:
        matches_calls.append((args, kwargs))
        return True

    monkeypatch.setattr(pgadmin_container, "container_matches", matches)
    monkeypatch.setattr(
        pgadmin_files,
        "prepare_files",
        lambda **_kwargs: pytest.fail("stale fingerprint mutated pgAdmin files"),
    )
    monkeypatch.setattr(
        pgadmin_container,
        "remove_container",
        lambda *_args, **_kwargs: pytest.fail("stale fingerprint removed a live container"),
    )

    with pytest.raises(StalePlanError, match="fingerprint key changed"):
        pgadmin.PgAdminProvisioningPhase(
            instance=instance,
            cluster=cluster,
            database="demo",
            captured_identity=identity,
            captured_paths=paths,
            captured_port=5050,
            captured_fingerprint=pgadmin_files.PgAdminFingerprintInputs(
                fingerprint="captured-hmac",
                key=b"b" * 32,
            ),
        )._provision_carrier()

    assert not inspect_calls
    assert not matches_calls
    assert not paths.pgpass.exists()


def test_reconciliation_phase_gap_rejects_changed_key_before_reinspection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A phase handle cannot use a persisted key changed after provisioning."""
    from odoo_instance_sdk.internal.postgres_compose import ComposeRunner
    from odoo_instance_sdk.internal.proc import PreparedAction, Step

    paths = _paths(tmp_path)
    monkeypatch.setattr(pgadmin_files, "get_data_root", lambda *, ensure_exists: tmp_path)
    paths.private_dir.mkdir(parents=True)
    key = b"a" * 32
    (paths.private_dir / ".fingerprint-key").write_bytes(key)
    (paths.private_dir / ".fingerprint-key").chmod(0o600)
    preparation = pgadmin_files.PgAdminPreparation(
        paths=paths,
        fingerprint="captured-hmac",
        port=5050,
        container_name=pgadmin_files.PGADMIN_CONTAINER_NAME,
        mounts=pgadmin_files.pgadmin_mounts(paths),
    )
    steps: tuple[Step, ...] = (
        pgadmin_container.reconciliation_inspect_step(),
        PreparedAction(
            step_id="pgadmin.reconciliation.port.revalidate",
            action="revalidate",
            read_only=True,
        ),
        *pgadmin_container.reconciliation_steps(
            paths=paths,
            port=5050,
            network="project_default",
            fingerprint="captured-hmac",
            secret_values=("captured-hmac", "database-secret"),
        ),
    )
    executor = RecordingExecutor()
    carrier = pgadmin._PgAdminReconciliationCarrier(
        preparation=preparation,
        runner=cast("ComposeRunner", object()),
        network="project_default",
        database="demo",
        timeout=1.0,
        steps=steps,
        executor=executor,
        fingerprint_key=key,
    )
    inspect_calls: list[object] = []
    monkeypatch.setattr(
        pgadmin_container,
        "inspect_container",
        lambda *args, **kwargs: inspect_calls.append((args, kwargs)),
    )
    (paths.private_dir / ".fingerprint-key").write_bytes(b"b" * 32)
    (paths.private_dir / ".fingerprint-key").chmod(0o600)

    with pytest.raises(StalePlanError, match="fingerprint key changed") as exc_info:
        carrier.reconciliation_command().run()

    assert str(exc_info.value) == "captured pgAdmin fingerprint key changed before execution"
    assert not inspect_calls
    assert not paths.pgpass.exists()
    assert not executor.executed


@pytest.mark.parametrize(
    "mutation", ["key-mode", "key-symlink", "pgpass-mode", "pgpass-symlink", "pgpass-content"]
)
def test_reconciliation_phase_gap_revalidates_saved_private_state(
    mutation: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A changed saved preparation fails before the continuation's Docker probe."""
    paths = _paths(tmp_path)
    monkeypatch.setattr(pgadmin_files.PgAdminPaths, "from_defaults", classmethod(lambda cls: paths))
    monkeypatch.setattr(pgadmin_files, "get_data_root", lambda *, ensure_exists: tmp_path)
    monkeypatch.setattr(pgadmin_files, "_linux", lambda: False)
    monkeypatch.setattr(pgadmin, "probe_address", lambda *_args: AddressState.FREE)
    identity = pgadmin_files.PostgresIdentity(
        container_name="postgres-1",
        network="project_default",
        user="odoo",
        host="postgres-1",
    )
    cluster = type(
        "Cluster",
        (),
        {
            "compose_runner": object(),
            "compose_file": tmp_path / "compose.yaml",
            "compose_project_name": "project",
            "_user": "odoo",
        },
    )()
    monkeypatch.setattr(
        pgadmin_container, "resolve_postgres_identity", lambda *_args, **_kwargs: identity
    )
    instance = type("Instance", (), {"config": type("Config", (), {"db_password": "secret"})()})()
    executor = RecordingExecutor()
    inspections: list[str | None] = []

    def inspect(*_args: object, **kwargs: object) -> None:
        step_id = cast("str | None", kwargs.get("step_id"))
        inspections.append(step_id)
        context = active_context()
        if step_id is not None and context is not None and context.planned(step_id):
            context.skip(step_id)

    monkeypatch.setattr(pgadmin_container, "inspect_container", inspect)
    handle = pgadmin.PgAdminProvisioningPhase(
        instance=instance,
        cluster=cluster,
        database="demo",
        captured_identity=identity,
        captured_paths=paths,
        captured_port=5050,
        captured_fingerprint=pgadmin_files.PgAdminFingerprintInputs(
            fingerprint="captured-hmac", key=b"k" * 32
        ),
        executor=executor,
    ).provision()
    pgpass_before = paths.pgpass.read_bytes()
    key_path = paths.private_dir / ".fingerprint-key"
    if mutation == "key-mode":
        key_path.chmod(0o644)
    elif mutation == "key-symlink":
        outside = tmp_path / "outside-key"
        outside.write_bytes(key_path.read_bytes())
        key_path.unlink()
        key_path.symlink_to(outside)
    elif mutation == "pgpass-mode":
        paths.pgpass.chmod(0o600)
    elif mutation == "pgpass-symlink":
        outside = tmp_path / "outside-pgpass"
        outside.write_bytes(paths.pgpass.read_bytes())
        paths.pgpass.unlink()
        paths.pgpass.symlink_to(outside)
    else:
        paths.pgpass.write_bytes(b"changed\n")
        paths.pgpass.chmod(0o640)

    with pytest.raises(StalePlanError, match="preparation changed"):
        handle.reconciliation_command().run()

    assert inspections == [None]
    assert not executor.executed
    assert paths.pgpass.read_bytes() == (
        pgpass_before if mutation != "pgpass-content" else b"changed\n"
    )


@pytest.mark.parametrize("managed", [None, "false"])
def test_unmanaged_existing_pgadmin_blocks_credential_preparation(
    managed: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An existing non-owned container cannot authorize private file access."""
    paths = _paths(tmp_path)
    identity = pgadmin_files.PostgresIdentity(
        container_name="postgres-1",
        network="project_default",
        user="odoo",
        host="postgres-1",
    )
    labels = {pgadmin_files.PGADMIN_LABEL_FINGERPRINT: "captured-hmac"}
    if managed is not None:
        labels[pgadmin_files.PGADMIN_LABEL_MANAGED] = managed
    current = {"Config": {"Labels": labels}}
    cluster = type(
        "Cluster",
        (),
        {
            "compose_runner": object(),
            "compose_file": tmp_path / "compose.yaml",
            "compose_project_name": "project",
            "_user": "odoo",
        },
    )()
    instance = type("Instance", (), {"config": type("Config", (), {"db_password": "secret"})()})()
    monkeypatch.setattr(
        pgadmin_container, "resolve_postgres_identity", lambda *_args, **_kwargs: identity
    )
    monkeypatch.setattr(pgadmin_container, "inspect_container", lambda *_args, **_kwargs: current)
    monkeypatch.setattr(
        pgadmin_files,
        "prepare_files",
        lambda **_kwargs: pytest.fail("unmanaged pgAdmin reached credential preparation"),
    )

    with pytest.raises(PgAdminUnavailableError, match="pgAdmin is unavailable"):
        pgadmin.PgAdminProvisioningPhase(
            instance=instance,
            cluster=cluster,
            database="demo",
            captured_identity=identity,
            captured_paths=paths,
            captured_fingerprint=pgadmin_files.PgAdminFingerprintInputs(
                fingerprint="captured-hmac",
                key=b"c" * 32,
            ),
        )._provision_carrier()

    assert not paths.pgpass.exists()


def test_captured_and_stale_fingerprints_are_redacted_everywhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import msgspec

    old = "old-hmac-" + "1" * 56
    new = "new-hmac-" + "2" * 56
    steps = pgadmin_container.reconciliation_steps(
        paths=_paths(tmp_path),
        port=5050,
        network="project_default",
        fingerprint=old,
        secret_values=(old, new, "database-password"),
    )
    run_step = next(step for step in steps if step.step_id == "pgadmin.container.run")
    plan = ExecutionPlan(steps=tuple(step.public_projection() for step in steps))
    serialized = msgspec.json.encode(plan).decode()
    assert old not in repr(plan)
    assert new not in repr(plan)
    assert old not in serialized
    assert new not in serialized

    def fail_spawn(*_args: object, **_kwargs: object) -> object:
        raise OSError(f"captured={old} stale={new}")

    monkeypatch.setattr(subprocess, "run", fail_spawn)
    with pytest.raises(ProcessSpawnError) as exc_info:
        SubprocessExecutor().execute(run_step)
    assert old not in repr(exc_info.value)
    assert new not in repr(exc_info.value)


def test_captured_port_conflict_fails_before_pgadmin_file_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    identity = pgadmin_files.PostgresIdentity(
        container_name="postgres-1",
        network="project_default",
        user="odoo",
        host="postgres-1",
    )
    cluster = type(
        "Cluster",
        (),
        {
            "compose_runner": object(),
            "compose_file": tmp_path / "compose.yaml",
            "compose_project_name": "project",
            "_user": "odoo",
        },
    )()
    instance = type("Instance", (), {"config": type("Config", (), {"db_password": "secret"})()})()

    monkeypatch.setattr(pgadmin_files.PgAdminPaths, "from_defaults", classmethod(lambda cls: paths))
    monkeypatch.setattr(
        pgadmin_container,
        "resolve_postgres_identity",
        lambda *_args, **_kwargs: identity,
    )
    monkeypatch.setattr(pgadmin_container, "inspect_container", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pgadmin, "probe_address", lambda *_args: AddressState.OCCUPIED)
    monkeypatch.setattr(
        pgadmin_files,
        "prepare_files",
        lambda **_kwargs: pytest.fail("captured port conflict mutated pgAdmin files"),
    )

    with pytest.raises(StalePlanError, match="loopback port"):
        pgadmin.open_pgadmin_phase(
            instance=instance,
            cluster=cluster,
            database="demo",
            captured_identity=identity,
            captured_paths=paths,
            captured_port=5050,
        )

    assert not paths.root.exists()


def test_captured_identity_mismatch_is_a_sanitized_stale_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    expected = pgadmin_files.PostgresIdentity(
        container_name="postgres-1",
        network="project_default",
        user="odoo",
        host="postgres-1",
    )
    actual = pgadmin_files.PostgresIdentity(
        container_name="other-postgres-1",
        network="other_default",
        user="odoo",
        host="other-postgres-1",
    )
    cluster = type(
        "Cluster",
        (),
        {
            "compose_runner": object(),
            "compose_file": tmp_path / "compose.yaml",
            "compose_project_name": "project",
            "_user": "odoo",
        },
    )()
    instance = type("Instance", (), {"config": type("Config", (), {"db_password": "secret"})()})()
    monkeypatch.setattr(
        pgadmin_container, "resolve_postgres_identity", lambda *_args, **_kwargs: actual
    )

    with pytest.raises(StalePlanError) as exc_info:
        pgadmin.open_pgadmin_phase(
            instance=instance,
            cluster=cluster,
            database="demo",
            captured_identity=expected,
            captured_paths=paths,
        )

    assert str(exc_info.value) == "captured pgAdmin identity changed before execution"
    assert "other-postgres" not in repr(exc_info.value)
    assert not paths.root.exists()


def test_phase_result_reinspects_after_phase_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A completed phase cannot leak its old container snapshot to reconciliation."""
    paths = _paths(tmp_path)
    identity = pgadmin_files.PostgresIdentity(
        container_name="postgres-1",
        network="project_default",
        user="odoo",
        host="postgres-1",
    )
    cluster = type(
        "Cluster",
        (),
        {
            "compose_runner": object(),
            "compose_file": tmp_path / "compose.yaml",
            "compose_project_name": "project",
            "_user": "odoo",
        },
    )()
    instance = type("Instance", (), {"config": type("Config", (), {"db_password": "secret"})()})()
    inspections: list[str | None] = []
    reconciliation_deadlines: list[float] = []
    reconciled: list[dict[str, object] | None] = []

    monkeypatch.setattr(pgadmin_files.PgAdminPaths, "from_defaults", classmethod(lambda cls: paths))
    monkeypatch.setattr(
        pgadmin_container,
        "resolve_postgres_identity",
        lambda *_args, **_kwargs: identity,
    )

    def inspect(*_args: object, **kwargs: object) -> dict[str, object] | None:
        step_id = cast("str | None", kwargs.get("step_id"))
        inspections.append(step_id)
        context = active_context()
        if step_id is not None and context is not None and context.planned(step_id):
            context.skip(step_id)
        if step_id == "pgadmin.reconciliation.inspect.0":
            reconciliation_deadlines.append(cast("float", kwargs["deadline"]))
            return {"Id": "container-created-after-phase"}
        return None

    monkeypatch.setattr(pgadmin_container, "inspect_container", inspect)
    monkeypatch.setattr(pgadmin, "probe_address", lambda *_args: AddressState.FREE)
    monkeypatch.setattr(pgadmin_files, "_linux", lambda: False)
    monkeypatch.setattr(pgadmin_files, "get_data_root", lambda *, ensure_exists: tmp_path)

    def reconcile(*_args: object, **kwargs: object) -> PgAdminOpenResult:
        reconciled.append(cast("dict[str, object] | None", kwargs["current_container"]))
        context = active_context()
        assert context is not None
        for step_id in (
            "pgadmin.container.inspect.1",
            "pgadmin.container.remove",
            "pgadmin.container.run",
            "pgadmin.container.refresh.inspect",
            "pgadmin.container.refresh",
            "pgadmin.container.verify",
            "pgadmin.container.inspect.2",
            "pgadmin.container.cleanup.remove",
        ):
            if context.planned(step_id) and not context.consumed(step_id):
                context.skip(step_id)
        return PgAdminOpenResult(
            state=PgAdminOpenState.REUSED,
            url="http://127.0.0.1:5050",
        )

    monkeypatch.setattr(pgadmin_container, "reconcile_container", reconcile)
    phase = pgadmin.PgAdminProvisioningPhase(
        instance=instance,
        cluster=cluster,
        database="demo",
        captured_identity=identity,
        captured_paths=paths,
        captured_port=5050,
    )

    handle = phase.provision()
    assert not hasattr(handle, "current_container")
    assert not hasattr(handle, "steps")
    assert "secret" not in repr(handle)
    assert not hasattr(phase, "run")
    reconciliation = handle.reconciliation_command()
    ordered_ids = [step.step_id for step in reconciliation.plan.steps]
    assert ordered_ids[:2] == [
        "pgadmin.reconciliation.inspect.0",
        "pgadmin.reconciliation.port.revalidate",
    ]
    assert ordered_ids.index("pgadmin.reconciliation.port.revalidate") < ordered_ids.index(
        "pgadmin.container.run"
    )

    result = reconciliation.run()
    time.sleep(0.01)
    repeated = reconciliation.run()

    assert result.state is PgAdminOpenState.REUSED
    assert repeated.state is PgAdminOpenState.REUSED
    assert inspections == [
        None,
        "pgadmin.reconciliation.inspect.0",
        "pgadmin.reconciliation.inspect.0",
    ]
    assert reconciled == [
        {"Id": "container-created-after-phase"},
        {"Id": "container-created-after-phase"},
    ]
    assert reconciliation_deadlines[1] > reconciliation_deadlines[0]


def test_open_pgadmin_lifecycle_uses_backend_identity_and_secret_free_docker_args(  # noqa: C901
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    monkeypatch.setattr(pgadmin_files.PgAdminPaths, "from_defaults", classmethod(lambda cls: paths))
    monkeypatch.setattr(pgadmin_files, "get_data_root", lambda *, ensure_exists: tmp_path)
    monkeypatch.setattr(pgadmin_files, "_linux", lambda: False)
    monkeypatch.setattr(pgadmin_container, "_wait_ready", lambda port, *, deadline: None)

    postgres_inspect = {
        "Name": "/odcli_pg_project-postgres-1",
        "Config": {
            "User": "odoo",
            "Labels": {"com.docker.compose.project": "odcli_pg_project"},
        },
        "NetworkSettings": {"Networks": {"odcli_pg_project_default": {}}},
    }

    class Runner:
        requires_docker = False

        def __init__(self) -> None:
            self.calls: list[list[str]] = []
            self.pgadmin_inspect: dict[str, object] | None = None

        def execute(self, step: PreparedStep) -> ProcessResult:
            result = self.run(list(step.argv), timeout=step.timeout)
            return ProcessResult(
                step.argv,
                result.returncode,
                result.stdout,
                result.stderr,
                0.0,
                step.cwd,
                step.environment,
            )

        def spawn(self, step: PreparedStep) -> ProcessHandle:
            del step
            raise AssertionError("pgAdmin does not spawn finite Docker steps")

        def run(self, args: list[str], **_: object) -> subprocess.CompletedProcess[str]:  # noqa: C901
            self.calls.append(list(args))
            if "ps" in args:
                return subprocess.CompletedProcess(
                    args, 0, '{"Service":"postgres","ID":"pgid"}\n', ""
                )
            if args[1:3] == ["inspect", "--format"] and args[-1] == "pgid":
                return subprocess.CompletedProcess(args, 0, json.dumps([postgres_inspect]), "")
            if args[1:4] == ["network", "inspect", "--format"]:
                return subprocess.CompletedProcess(
                    args,
                    0,
                    json.dumps([{"Labels": {"com.docker.compose.project": "odcli_pg_project"}}]),
                    "",
                )
            if (
                args[1:3] == ["inspect", "--format"]
                and args[-1] == pgadmin_files.PGADMIN_CONTAINER_NAME
            ):
                if self.pgadmin_inspect is None:
                    return subprocess.CompletedProcess(
                        args, 1, "", "Error: No such object: pgadmin"
                    )
                return subprocess.CompletedProcess(args, 0, json.dumps([self.pgadmin_inspect]), "")
            if args[1:3] == ["inspect", "--format"] and args[-1] == "pgadmin-id":
                assert self.pgadmin_inspect is not None
                return subprocess.CompletedProcess(args, 0, json.dumps([self.pgadmin_inspect]), "")
            if args[1:2] == ["exec"]:
                return subprocess.CompletedProcess(
                    args,
                    0,
                    json.dumps(
                        [
                            {
                                "host": "odcli_pg_project-postgres-1",
                                "port": 5432,
                                "username": "odoo",
                                "maintenance_db": "demo",
                                "db_res": "demo",
                            }
                        ]
                    ),
                    "",
                )
            if args[1:2] == ["run"]:
                labels: dict[str, str] = {}
                for index, value in enumerate(args):
                    if value == "--label":
                        key, label_value = args[index + 1].split("=", 1)
                        labels[key] = label_value
                self.pgadmin_inspect = {
                    "Id": "pgadmin-id",
                    "Config": {"Labels": labels},
                }
                return subprocess.CompletedProcess(args, 0, "pgadmin-id\n", "")
            return subprocess.CompletedProcess(args, 0, "", "")

    runner = Runner()
    cluster = type(
        "Cluster",
        (),
        {
            "compose_runner": runner,
            "compose_file": tmp_path / "compose.yaml",
            "compose_project_name": "odcli_pg_project",
            "_user": None,
        },
    )()
    instance = type(
        "Instance", (), {"config": type("Config", (), {"db_password": "db-secret"})()}
    )()

    result = (
        pgadmin.open_pgadmin_phase(
            instance=instance,
            cluster=cluster,
            database="demo",
            executor=runner,
        )
        .reconciliation_command()
        .run()
    )

    assert result.state.value == "started"
    assert result.url == "http://127.0.0.1:5050"
    run_call = next(call for call in runner.calls if call[1] == "run")
    rendered = " ".join(run_call)
    first_label = next(
        run_call[index + 1].split("=", 1)[1]
        for index, value in enumerate(run_call[:-1])
        if value == "--label"
        and run_call[index + 1].startswith(f"{pgadmin_files.PGADMIN_LABEL_FINGERPRINT}=")
    )
    assert (
        first_label
        == pgadmin_files.execution_fingerprint_inputs(
            paths,
            pgadmin_files.PostgresIdentity(
                container_name="odcli_pg_project-postgres-1",
                network="odcli_pg_project_default",
                user="odoo",
                host="odcli_pg_project-postgres-1",
            ),
            "demo",
            "db-secret",
        ).fingerprint
    )
    assert "db-secret" not in rendered
    assert "db-secret" not in " ".join(" ".join(call) for call in runner.calls)
    assert "--network" in run_call
    assert "odcli_pg_project_default" in run_call
    assert "--publish" in run_call
    assert "127.0.0.1:5050:80" in run_call
    assert "--user" in run_call and "5050" in run_call
    assert "--mount" in run_call
    assert "docker.sock" not in rendered
    assert "MaintenanceDB" in paths.servers_json.read_text()
    assert '"MaintenanceDB":"demo"' in paths.servers_json.read_text()
    assert '"DBRestriction":"demo"' in paths.servers_json.read_text()


def test_concurrent_lifecycle_calls_create_one_container_at_boundary(  # noqa: C901
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    monkeypatch.setattr(pgadmin_files.PgAdminPaths, "from_defaults", classmethod(lambda cls: paths))
    monkeypatch.setattr(pgadmin_files, "get_data_root", lambda *, ensure_exists: tmp_path)
    monkeypatch.setattr(pgadmin_files, "_linux", lambda: False)
    monkeypatch.setattr(pgadmin_container, "_wait_ready", lambda *_args, **_kwargs: None)

    postgres_inspect = {
        "Name": "/odcli_pg_project-postgres-1",
        "Config": {
            "User": "odoo",
            "Labels": {"com.docker.compose.project": "odcli_pg_project"},
        },
        "NetworkSettings": {"Networks": {"odcli_pg_project_default": {}}},
    }

    class Runner:
        requires_docker = False

        def __init__(self) -> None:
            self.calls: list[list[str]] = []
            self.create_count = 0
            self._container: dict[str, object] | None = None
            self._lock = threading.Lock()

        def execute(self, step: PreparedStep) -> ProcessResult:
            result = self.run(list(step.argv), timeout=step.timeout)
            return ProcessResult(
                step.argv,
                result.returncode,
                result.stdout,
                result.stderr,
                0.0,
                step.cwd,
                step.environment,
            )

        def spawn(self, step: PreparedStep) -> ProcessHandle:
            del step
            raise AssertionError("pgAdmin does not spawn finite Docker steps")

        def run(self, args: list[str], **_: object) -> subprocess.CompletedProcess[str]:  # noqa: C901
            with self._lock:
                self.calls.append(list(args))
                if "ps" in args:
                    return subprocess.CompletedProcess(
                        args, 0, '{"Service":"postgres","ID":"pgid"}\n', ""
                    )
                if args[1:3] == ["inspect", "--format"] and args[-1] == "pgid":
                    return subprocess.CompletedProcess(args, 0, json.dumps([postgres_inspect]), "")
                if args[1:4] == ["network", "inspect", "--format"]:
                    return subprocess.CompletedProcess(
                        args,
                        0,
                        json.dumps(
                            [{"Labels": {"com.docker.compose.project": "odcli_pg_project"}}]
                        ),
                        "",
                    )
                if args[1:3] == ["inspect", "--format"]:
                    if self._container is None:
                        return subprocess.CompletedProcess(args, 1, "", "No such object")
                    return subprocess.CompletedProcess(args, 0, json.dumps([self._container]), "")
                if args[1:2] == ["run"]:
                    self.create_count += 1
                    labels: dict[str, str] = {}
                    for index, value in enumerate(args):
                        if value == "--label":
                            key, label_value = args[index + 1].split("=", 1)
                            labels[key] = label_value
                    mounts: list[dict[str, object]] = []
                    for index, value in enumerate(args):
                        if value == "--mount":
                            mount = args[index + 1].removeprefix("type=bind,")
                            fields = dict(
                                item.split("=", 1) for item in mount.split(",") if "=" in item
                            )
                            mounts.append(
                                {
                                    "Source": fields["source"],
                                    "Destination": fields["destination"],
                                    "RW": "readonly" not in mount,
                                }
                            )
                    port = next(value for value in args if value.startswith("127.0.0.1:"))
                    host_port = port.split(":", 2)[1]
                    network = args[args.index("--network") + 1]
                    self._container = {
                        "Id": "pgadmin-id",
                        "Name": f"/{pgadmin_files.PGADMIN_CONTAINER_NAME}",
                        "Config": {
                            "Image": pgadmin_files.PGADMIN_IMAGE,
                            "User": str(pgadmin_files.PGADMIN_RUNTIME_UID),
                            "Env": [
                                "PGADMIN_CONFIG_SERVER_MODE=False",
                                "PGADMIN_REPLACE_SERVERS_ON_STARTUP=True",
                                f"PGADMIN_DEFAULT_EMAIL={pgadmin_files.PGADMIN_DEFAULT_EMAIL}",
                                f"PGADMIN_DEFAULT_PASSWORD_FILE={pgadmin_files.PGADMIN_PASSWORD_DESTINATION}",
                                f"PGPASS_FILE={pgadmin_files.PGADMIN_PGPASS_DESTINATION}",
                            ],
                            "Labels": labels,
                        },
                        "State": {"Running": True},
                        "NetworkSettings": {"Networks": {network: {}}},
                        "Mounts": mounts,
                        "HostConfig": {
                            "PortBindings": {
                                "80/tcp": [{"HostIp": "127.0.0.1", "HostPort": host_port}]
                            }
                        },
                    }
                    return subprocess.CompletedProcess(args, 0, "pgadmin-id\n", "")
                if args[1:2] == ["exec"]:
                    return subprocess.CompletedProcess(
                        args,
                        0,
                        json.dumps(
                            [
                                {
                                    "host": "odcli_pg_project-postgres-1",
                                    "port": 5432,
                                    "username": "odoo",
                                    "maintenance_db": "demo",
                                    "db_res": "demo",
                                }
                            ]
                        ),
                        "",
                    )
                pytest.fail(f"unexpected docker argv: {args}")

    runner = Runner()
    cluster = type(
        "Cluster",
        (),
        {
            "compose_runner": runner,
            "compose_file": tmp_path / "compose.yaml",
            "compose_project_name": "odcli_pg_project",
            "_user": None,
        },
    )()
    instance = type("Instance", (), {"config": type("Config", (), {"db_password": "secret"})()})()
    start = threading.Barrier(2)

    def invoke() -> PgAdminOpenResult:
        start.wait()
        return (
            pgadmin.open_pgadmin_phase(
                instance=instance,
                cluster=cluster,
                database="demo",
                executor=runner,
            )
            .reconciliation_command()
            .run()
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: invoke(), range(2)))

    assert [result.state for result in results].count(PgAdminOpenState.STARTED) == 1
    assert [result.state for result in results].count(PgAdminOpenState.REUSED) == 1
    assert runner.create_count == 1
    assert not any(call[1:2] == ["rm"] for call in runner.calls)
