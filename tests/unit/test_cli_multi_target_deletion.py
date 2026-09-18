from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch
from zipfile import ZipFile

from click.testing import CliRunner

if TYPE_CHECKING:
    import pytest
    from click.testing import Result

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

BACKUP_ID_A = "00000000-0000-0000-0000-000000000007"
BACKUP_ID_B = "00000000-0000-0000-0000-000000000008"
BACKUP_ID_C = "00000000-0000-0000-0000-000000000009"


def _seed_backup(
    tmp_path: Path,
    backup_id: str,
    *,
    database_name: str = "demo",
) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "catalog.sqlite3"
    backup_path = tmp_path / f"backup-{backup_id}.zip"
    with ZipFile(backup_path, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"db_name": database_name}))
        archive.writestr("dump.sql", "-- test")
    catalog = BackupCatalog(db_path=db_path)
    catalog.start_download(
        backup_id,
        "http://localhost:8069",
        database_name,
        "zip",
        True,
        backup_path,
    )
    catalog.success_download(backup_id, backup_path.name, backup_path.stat().st_size, "")
    catalog.close()
    return db_path, backup_path


def _seed_two_backups(tmp_path: Path) -> tuple[Path, Path, Path]:
    db_path = tmp_path / "catalog.sqlite3"
    backup_a = tmp_path / f"backup-{BACKUP_ID_A}.zip"
    backup_b = tmp_path / f"backup-{BACKUP_ID_B}.zip"
    for backup_id, path in ((BACKUP_ID_A, backup_a), (BACKUP_ID_B, backup_b)):
        with ZipFile(path, "w") as archive:
            archive.writestr("manifest.json", json.dumps({"db_name": "demo"}))
            archive.writestr("dump.sql", "-- test")
    catalog = BackupCatalog(db_path=db_path)
    for backup_id, path in ((BACKUP_ID_A, backup_a), (BACKUP_ID_B, backup_b)):
        catalog.start_download(backup_id, "http://localhost:8069", "demo", "zip", True, path)
        catalog.success_download(backup_id, path.name, path.stat().st_size, "")
    catalog.close()
    return db_path, backup_a, backup_b


def _invoke(
    monkeypatch: pytest.MonkeyPatch,
    db_path: Path,
    args: list[str],
    *,
    input: str | None = None,
) -> Result:
    monkeypatch.setattr("odoo_instance_sdk.cli.get_catalog_path", lambda **_kwargs: db_path)
    return CliRunner().invoke(cli, args, input=input)


def test_backup_rm_multi_target_dry_run_emits_ordered_aggregate_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_a, path_a, path_b = _seed_two_backups(tmp_path)
    result = _invoke(
        monkeypatch,
        db_a,
        ["backup", "rm", BACKUP_ID_A, BACKUP_ID_B, "--dry-run", "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    targets = payload["result"]["targets"]
    assert [t["target"] for t in targets] == [BACKUP_ID_A, BACKUP_ID_B]
    assert all("plan" in t for t in targets)
    assert path_a.is_file() and path_b.is_file()


def test_backup_rm_multi_target_yes_deletes_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_a, path_a, path_b = _seed_two_backups(tmp_path)
    result = _invoke(
        monkeypatch,
        db_a,
        ["backup", "rm", BACKUP_ID_A, BACKUP_ID_B, "--yes", "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    targets = payload["result"]["targets"]
    assert all(t["ok"] for t in targets)
    assert not path_a.exists() and not path_b.exists()


def test_backup_rm_multi_target_unknown_aborts_before_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_a, path_a, _path_b = _seed_two_backups(tmp_path)
    unknown = str(uuid.uuid4())
    result = _invoke(
        monkeypatch,
        db_a,
        ["backup", "rm", BACKUP_ID_A, unknown, "--yes", "--format", "json"],
    )
    assert result.exit_code == 1, result.output
    assert path_a.is_file(), "no mutation when one target is unknown"


def test_backup_rm_multi_target_duplicate_aborts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_a, path_a, _ = _seed_two_backups(tmp_path)
    result = _invoke(
        monkeypatch,
        db_a,
        ["backup", "rm", BACKUP_ID_A, BACKUP_ID_A, "--yes", "--format", "json"],
    )
    assert result.exit_code == 2, result.output
    assert path_a.is_file()


def test_backup_rm_multi_target_partial_failure_non_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_a, _path_a, _path_b = _seed_two_backups(tmp_path)
    call_count = {"n": 0}

    def _fake_resource(catalog: BackupCatalog) -> tuple[MagicMock, object]:

        class _FakeResource:
            def delete_command(self, backup: object) -> object:
                call_count["n"] += 1
                if call_count["n"] == 2:
                    raise RuntimeError("boom on second target")

                class _Cmd:
                    def run(self) -> object:
                        from datetime import UTC, datetime

                        from odoo_instance_sdk.models import BackupDeletionResult

                        return BackupDeletionResult(
                            file_existed=True,
                            already_deleted=False,
                            deleted_at=datetime.now(UTC),
                        )

                return _Cmd()

        return MagicMock(), _FakeResource()

    with patch(
        "odoo_instance_sdk.commands.backup._backup_resource",
        side_effect=_fake_resource,
    ):
        result = _invoke(
            monkeypatch,
            db_a,
            ["backup", "rm", BACKUP_ID_A, BACKUP_ID_B, "--yes", "--format", "json"],
        )

    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    targets = payload["context"]["targets"]
    assert targets[0]["ok"] is True
    assert targets[1]["ok"] is False


def test_backup_rm_single_target_backward_compat_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path, backup_path = _seed_backup(tmp_path, BACKUP_ID_A)
    result = _invoke(
        monkeypatch,
        db_path,
        ["backup", "rm", BACKUP_ID_A, "--dry-run", "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    plan = json.loads(result.stdout)["result"]["plan"]
    assert plan["backup_id"] == BACKUP_ID_A
    assert backup_path.is_file()


def test_backup_rm_single_target_backward_compat_yes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path, backup_path = _seed_backup(tmp_path, BACKUP_ID_A)
    result = _invoke(
        monkeypatch,
        db_path,
        ["backup", "rm", BACKUP_ID_A, "--yes", "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    assert not backup_path.exists()


def test_backup_rm_machine_without_yes_emits_confirmation_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path, _ = _seed_backup(tmp_path, BACKUP_ID_A)
    result = _invoke(
        monkeypatch,
        db_path,
        ["backup", "rm", BACKUP_ID_A, BACKUP_ID_B, "--format", "json"],
    )
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "confirmation_required"


def test_db_rm_multi_target_dry_run_emits_ordered_aggregate_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from odoo_instance_sdk.commands.output import action_command

    command = action_command("db.drop.noop", lambda: None)
    instance = MagicMock()
    instance._postgres_cluster.endpoint = "127.0.0.1:5432"
    builder = MagicMock(return_value=command)
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.pg._database_instance", lambda _ctx: (None, instance)
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.db.resolve_project_path", lambda _ctx: Path.cwd()
    )
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.drop.build_database_drop_command", builder)

    result = CliRunner().invoke(cli, ["db", "rm", "db1", "db2", "--dry-run", "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    targets = payload["result"]["targets"]
    assert [t["target"] for t in targets] == ["db1", "db2"]
    assert builder.call_count == 2


def test_db_rm_multi_target_yes_drops_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from odoo_instance_sdk.internal.pg.drop import DatabaseDropResult

    instance = MagicMock()
    instance._postgres_cluster.endpoint = "127.0.0.1:5432"

    class _Cmd:
        def __init__(self, name: str) -> None:
            self._name = name

        def run(self) -> DatabaseDropResult:
            return DatabaseDropResult(database=self._name, cluster="127.0.0.1:5432")

    def _builder(
        _instance: object,
        _root: object,
        name: str,
        **_kwargs: object,
    ) -> _Cmd:
        return _Cmd(name)

    monkeypatch.setattr(
        "odoo_instance_sdk.commands.pg._database_instance", lambda _ctx: (None, instance)
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.db.resolve_project_path", lambda _ctx: Path.cwd()
    )
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.drop.build_database_drop_command", _builder)

    result = CliRunner().invoke(cli, ["db", "rm", "db1", "db2", "--yes", "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    targets = payload["result"]["targets"]
    assert all(t["ok"] for t in targets)
    assert [t["result"]["database"] for t in targets] == ["db1", "db2"]


def test_db_rm_multi_target_duplicate_aborts(monkeypatch: pytest.MonkeyPatch) -> None:
    instance = MagicMock()
    instance._postgres_cluster.endpoint = "127.0.0.1:5432"
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.pg._database_instance", lambda _ctx: (None, instance)
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.db.resolve_project_path", lambda _ctx: Path.cwd()
    )
    builder = MagicMock()
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.drop.build_database_drop_command", builder)

    result = CliRunner().invoke(cli, ["db", "rm", "demo", "demo", "--yes", "--format", "json"])
    assert result.exit_code == 2, result.output
    builder.assert_not_called()


def test_db_rm_multi_target_planning_failure_aborts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = MagicMock()
    instance._postgres_cluster.endpoint = "127.0.0.1:5432"
    counter = {"n": 0}

    def _builder(*_args: object, **_kwargs: object) -> object:
        counter["n"] += 1
        if counter["n"] == 2:
            raise RuntimeError("db2 not found in cluster")
        return MagicMock()

    monkeypatch.setattr(
        "odoo_instance_sdk.commands.pg._database_instance", lambda _ctx: (None, instance)
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.db.resolve_project_path", lambda _ctx: Path.cwd()
    )
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.drop.build_database_drop_command", _builder)

    result = CliRunner().invoke(cli, ["db", "rm", "db1", "db2", "--yes", "--format", "json"])
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is False


def test_db_rm_single_target_backward_compat_dry_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from odoo_instance_sdk.commands.output import action_command

    command = action_command("db.drop.noop", lambda: None)
    instance = MagicMock()
    instance._postgres_cluster.endpoint = "127.0.0.1:5432"
    builder = MagicMock(return_value=command)
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.pg._database_instance", lambda _ctx: (None, instance)
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.db.resolve_project_path", lambda _ctx: Path.cwd()
    )
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.drop.build_database_drop_command", builder)

    result = CliRunner().invoke(cli, ["db", "rm", "demo", "--dry-run", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["dry_run"] is True
    builder.assert_called_once()


def test_db_rm_machine_without_yes_emits_confirmation_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = MagicMock()
    instance._postgres_cluster.endpoint = "127.0.0.1:5432"
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.pg._database_instance", lambda _ctx: (None, instance)
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.db.resolve_project_path", lambda _ctx: Path.cwd()
    )
    with patch(
        "odoo_instance_sdk.commands.pg._database_instance",
        side_effect=AssertionError("confirmation must precede resolution"),
    ):
        result = CliRunner().invoke(cli, ["db", "rm", "db1", "db2", "--format", "json"])
    assert result.exit_code == 1
    assert "confirmation_required" in result.output


def test_env_rm_multi_target_dry_run_emits_ordered_aggregate_plan(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    env_a = SimpleNamespace(
        id="env-a",
        name="demo-a",
        state="ready",
        branch="main",
        db_mode="shared",
        http_port=8069,
        worktree_path="/wt-a",
    )
    env_b = SimpleNamespace(
        id="env-b",
        name="demo-b",
        state="ready",
        branch="main",
        db_mode="shared",
        http_port=8070,
        worktree_path="/wt-b",
    )
    env_map = {"env-a": env_a, "env-b": env_b}

    client = MagicMock()
    client.environments.get.side_effect = lambda selector: env_map[selector]
    client.environments.remove_command.return_value = MagicMock(plan=MagicMock())

    def _plan_to_dict(plan: object) -> dict[str, object]:
        return {"steps": []}

    monkeypatch.setattr("odoo_instance_sdk.commands.env.list.model_to_dict", _plan_to_dict)

    with (
        patch("odoo_instance_sdk.commands.env.OdooClient", return_value=client),
        patch("odoo_instance_sdk.commands.env.resolve_project_path", return_value=tmp_path),
    ):
        result = CliRunner().invoke(
            cli, ["env", "rm", "env-a", "env-b", "--dry-run", "--format", "json"]
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    targets = payload["result"]["targets"]
    assert [t["target"] for t in targets] == ["env-a", "env-b"]


def test_env_rm_multi_target_yes_removes_all(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    env_a = SimpleNamespace(
        id="env-a",
        name="demo-a",
        state="ready",
        branch="main",
        db_mode="shared",
        http_port=8069,
        worktree_path="/wt-a",
    )
    env_b = SimpleNamespace(
        id="env-b",
        name="demo-b",
        state="ready",
        branch="main",
        db_mode="shared",
        http_port=8070,
        worktree_path="/wt-b",
    )
    env_map = {"env-a": env_a, "env-b": env_b}

    client = MagicMock()
    client.environments.get.side_effect = lambda selector: env_map[selector]
    command = MagicMock()
    command.run.return_value = None
    command.plan = MagicMock()
    client.environments.remove_command.return_value = command

    def _plan_to_dict(plan: object) -> dict[str, object]:
        return {"steps": []}

    monkeypatch.setattr("odoo_instance_sdk.commands.env.list.model_to_dict", _plan_to_dict)

    with (
        patch("odoo_instance_sdk.commands.env.OdooClient", return_value=client),
        patch("odoo_instance_sdk.commands.env.resolve_project_path", return_value=tmp_path),
        patch("odoo_instance_sdk.commands.env.list.click.confirm") as confirm,
    ):
        result = CliRunner().invoke(
            cli, ["env", "rm", "env-a", "env-b", "--yes", "--format", "json"]
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    targets = payload["result"]["targets"]
    assert all(t["ok"] for t in targets)
    confirm.assert_not_called()


def test_env_rm_multi_target_unknown_aborts(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_a = MagicMock(
        id="env-a",
        name="demo-a",
        state="ready",
        branch="main",
        db_mode="shared",
        http_port=8069,
        worktree_path="/wt-a",
    )
    client = MagicMock()
    client.environments.get.side_effect = lambda selector: (
        env_a if selector == "env-a" else (_ for _ in ()).throw(RuntimeError("env-b not found"))
    )

    with (
        patch("odoo_instance_sdk.commands.env.OdooClient", return_value=client),
        patch("odoo_instance_sdk.commands.env.resolve_project_path", return_value=tmp_path),
    ):
        result = CliRunner().invoke(
            cli, ["env", "rm", "env-a", "env-b", "--yes", "--format", "json"]
        )

    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    client.environments.remove_command.assert_not_called()


def test_env_rm_multi_target_duplicate_aborts(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = MagicMock()

    with (
        patch("odoo_instance_sdk.commands.env.OdooClient", return_value=client),
        patch("odoo_instance_sdk.commands.env.resolve_project_path", return_value=tmp_path),
    ):
        result = CliRunner().invoke(
            cli, ["env", "rm", "env-a", "env-a", "--yes", "--format", "json"]
        )

    assert result.exit_code == 1, result.output
    client.environments.remove_command.assert_not_called()


def test_env_rm_multi_target_partial_failure_non_zero(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    env_a = SimpleNamespace(
        id="env-a",
        name="demo-a",
        state="ready",
        branch="main",
        db_mode="shared",
        http_port=8069,
        worktree_path="/wt-a",
    )
    env_b = SimpleNamespace(
        id="env-b",
        name="demo-b",
        state="ready",
        branch="main",
        db_mode="shared",
        http_port=8070,
        worktree_path="/wt-b",
    )
    env_map = {"env-a": env_a, "env-b": env_b}

    client = MagicMock()
    client.environments.get.side_effect = lambda selector: env_map[selector]
    call_count = {"n": 0}

    class _Cmd:
        def run(self) -> None:
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("boom on second target")

    client.environments.remove_command.return_value = _Cmd()

    def _plan_to_dict(plan: object) -> dict[str, object]:
        return {"steps": []}

    monkeypatch.setattr("odoo_instance_sdk.commands.env.list.model_to_dict", _plan_to_dict)

    with (
        patch("odoo_instance_sdk.commands.env.OdooClient", return_value=client),
        patch("odoo_instance_sdk.commands.env.resolve_project_path", return_value=tmp_path),
    ):
        result = CliRunner().invoke(
            cli, ["env", "rm", "env-a", "env-b", "--yes", "--format", "json"]
        )

    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    targets = payload["context"]["targets"]
    assert targets[0]["ok"] is True
    assert targets[1]["ok"] is False


def test_env_rm_single_target_backward_compat(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    env = SimpleNamespace(
        id="env-1",
        name="demo",
        state="ready",
        branch="main",
        db_mode="shared",
        http_port=8069,
        worktree_path="/worktree",
    )
    client = MagicMock()
    client.environments.get.return_value = env
    client.environments.remove_command.return_value = MagicMock(plan=MagicMock())

    def _plan_to_dict(plan: object) -> dict[str, object]:
        return {"steps": []}

    monkeypatch.setattr("odoo_instance_sdk.commands.env.list.model_to_dict", _plan_to_dict)

    with (
        patch("odoo_instance_sdk.commands.env.OdooClient", return_value=client),
        patch("odoo_instance_sdk.commands.env.resolve_project_path", return_value=tmp_path),
        patch("odoo_instance_sdk.commands.env.list.click.confirm") as confirm,
    ):
        result = CliRunner().invoke(cli, ["env", "rm", "env-1", "--yes", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert result.output.count("schema_version") == 1
    client.environments.remove_command.assert_called_once_with(env)
    confirm.assert_not_called()


def test_env_rm_machine_without_yes_emits_confirmation_required(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    env_a = SimpleNamespace(
        id="env-a",
        name="demo-a",
        state="ready",
        branch="main",
        db_mode="shared",
        http_port=8069,
        worktree_path="/wt-a",
    )
    env_b = SimpleNamespace(
        id="env-b",
        name="demo-b",
        state="ready",
        branch="main",
        db_mode="shared",
        http_port=8070,
        worktree_path="/wt-b",
    )
    env_map = {"env-a": env_a, "env-b": env_b}
    client = MagicMock()
    client.environments.get.side_effect = lambda selector: env_map[selector]

    with (
        patch("odoo_instance_sdk.commands.env.OdooClient", return_value=client),
        patch("odoo_instance_sdk.commands.env.resolve_project_path", return_value=tmp_path),
        patch("odoo_instance_sdk.commands.env.list.click.confirm") as confirm,
    ):
        result = CliRunner().invoke(cli, ["env", "rm", "env-a", "env-b", "--format", "json"])

    assert result.exit_code == 1, result.output
    assert "confirmation_required" in result.output
    confirm.assert_not_called()
    client.environments.remove_command.assert_not_called()
