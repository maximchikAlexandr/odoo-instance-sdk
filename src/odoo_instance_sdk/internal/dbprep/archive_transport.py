from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.models import BackupFormat

if TYPE_CHECKING:
    from odoo_instance_sdk.internal.proc import PreparedAction, PreparedStep
    from odoo_instance_sdk.resources.instance import OdooInstance


def build_selected_backup_restore_steps(
    instance: OdooInstance,
    *,
    target_database: str,
    dump_path: Path,
    backup_format: BackupFormat = BackupFormat.ZIP,
) -> tuple[PreparedStep, PreparedStep | PreparedAction, PreparedStep]:
    """Build the preparation PostgreSQL steps for a stopped COPY."""
    from odoo_instance_sdk.internal.pg.builder import build_psql_specification
    from odoo_instance_sdk.internal.proc import PreparedAction, PreparedStep

    cluster = instance._postgres_cluster
    if cluster is None:
        raise ConfigError("selected restore requires a bound PostgreSQL cluster")
    user = instance.config.db_user or getattr(cluster, "_user", None)
    if user is None:
        raise ConfigError("selected restore requires a PostgreSQL user")
    create = build_psql_specification(
        step_id="database.replace.restore.create",
        host=cluster.endpoint_host,
        port=cluster.endpoint_port,
        user=user,
        password=instance.config.db_password,
        database="postgres",
        args=("-c", f'CREATE DATABASE "{target_database.replace(chr(34), chr(34) + chr(34))}";'),
        _trusted_args=("-t", "-A"),
        timeout=30.0,
        _read_only=False,
        _mutating=True,
    ).prepared_step
    if backup_format == BackupFormat.DUMP:
        executable = shutil.which("pg_restore")
        if executable is None:
            raise ConfigError("selected native dump restore requires pg_restore")
        validate: PreparedStep | PreparedAction = PreparedStep(
            step_id="database.replace.restore.validate",
            argv=(executable, "--list", str(dump_path)),
            cwd=str(instance.config.default_cwd),
            timeout=30.0,
            read_only=True,
            text=True,
        )
        restore = PreparedStep(
            step_id="database.replace.restore.pg-restore",
            argv=(
                executable,
                "--exit-on-error",
                "--single-transaction",
                "--host",
                cluster.endpoint_host,
                "--port",
                str(cluster.endpoint_port),
                "--username",
                user,
                "--dbname",
                target_database,
                str(dump_path),
            ),
            cwd=str(instance.config.default_cwd),
            environment=(
                ()
                if instance.config.db_password is None
                else (("PGPASSWORD", instance.config.db_password),)
            ),
            secret_values=(
                () if instance.config.db_password is None else (instance.config.db_password,)
            ),
            timeout=30.0,
            read_only=False,
            mutating=True,
            text=False,
        )
    else:
        validate = PreparedAction(
            step_id="database.replace.restore.validate",
            action="validate-odoo-zip-dump",
            description="Use the validated Odoo ZIP SQL transport",
            read_only=True,
        )
        restore = build_psql_specification(
            step_id="database.replace.restore.psql",
            host=cluster.endpoint_host,
            port=cluster.endpoint_port,
            user=user,
            password=instance.config.db_password,
            database=target_database,
            args=("--single-transaction", "--set", "ON_ERROR_STOP=1", "--file", str(dump_path)),
            timeout=30.0,
            _read_only=False,
            _mutating=True,
        ).prepared_step
    return create, validate, restore
