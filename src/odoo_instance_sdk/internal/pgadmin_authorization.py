"""Desktop master-password unlock and selected-database proof."""

from __future__ import annotations

import json
import subprocess

from odoo_instance_sdk.exceptions import PgAdminUnavailableError
from odoo_instance_sdk.internal import pgadmin_master_password
from odoo_instance_sdk.internal.pgadmin_files import (
    PGADMIN_CONTAINER_NAME,
    PgAdminPreparation,
    load_master_password,
    store_new_master_password,
)
from odoo_instance_sdk.internal.postgres_compose import ComposeRunner
from odoo_instance_sdk.internal.proc import active_context

_PROFILE_PROBE = (
    "import sqlite3\n"
    "from pathlib import Path\n"
    'path=Path("/var/lib/pgadmin/pgadmin4.db")\n'
    "if not path.is_file():\n"
    '    print("0")\n'
    "else:\n"
    "    try:\n"
    '        db=sqlite3.connect("file:" + path.as_posix() + "?mode=ro", uri=True)\n'
    '        row=db.execute("SELECT masterpass_check FROM user LIMIT 1").fetchone()\n'
    "    except sqlite3.Error:\n"
    "        raise SystemExit(1)\n"
    "    else:\n"
    '        print("1" if row is not None and row[0] not in (None, "") else "0")\n'
)
_SELECT_ONE = (
    "import sys,psycopg\n"
    "connection=psycopg.connect(host=sys.argv[1],port=int(sys.argv[2]),user=sys.argv[3],"
    "dbname=sys.argv[4],passfile='/var/lib/pgadmin/.pgpass')\n"
    "cursor=connection.cursor()\n"
    "cursor.execute('SELECT 1')\n"
    "row=cursor.fetchone()\n"
    "cursor.close()\n"
    "connection.close()\n"
    "raise SystemExit(0 if row == (1,) else 1)\n"
)


def probe_argv(container: str) -> tuple[str, ...]:
    """Return the read-only argv that reports whether a master password exists."""
    return ("docker", "exec", container, "/venv/bin/python3", "-c", _PROFILE_PROBE)


def authenticate_argv(
    container: str, host: str, port: int, user: str, database: str
) -> tuple[str, ...]:
    """Return the read-only argv that proves the selected database with SELECT 1."""
    return (
        "docker",
        "exec",
        container,
        "/venv/bin/python3",
        "-c",
        _SELECT_ONE,
        host,
        str(port),
        user,
        database,
    )


def authorize_selected_database(
    preparation: PgAdminPreparation,
    database: str,
    *,
    runner: ComposeRunner,
    deadline: float,
    planned: bool,
) -> None:
    """Unlock the desktop master password and prove the selected database.

    Args:
        preparation: Immutable pgAdmin file preparation.
        database: Selected maintenance database.
        runner: Docker command runner.
        deadline: Monotonic time at which the attempt must stop.
        planned: Whether this invocation is consuming a captured command.

    Raises:
        PgAdminUnavailableError: The profile cannot be unlocked or the query fails.
    """
    container = PGADMIN_CONTAINER_NAME if planned else preparation.container_name
    established = _profile_has_master_password(runner, container, deadline=deadline)
    password = load_master_password(preparation.paths)
    if password is None and established:
        raise PgAdminUnavailableError()
    if password is None:
        password = store_new_master_password(preparation.paths)
    context = active_context()
    if context is not None and context.planned("pgadmin.master_password.unlock"):
        context.action("pgadmin.master_password.unlock")
    elif planned:
        raise PgAdminUnavailableError()
    pgadmin_master_password.unlock_master_password(preparation.port, password, deadline=deadline)
    host, port, user = _selected_connection(preparation, database)
    result = _run_docker(
        runner,
        list(authenticate_argv(container, host, port, user, database)),
        deadline=deadline,
        step_id="pgadmin.container.authenticate",
    )
    if result.returncode != 0:
        raise PgAdminUnavailableError()


def _profile_has_master_password(runner: ComposeRunner, container: str, *, deadline: float) -> bool:
    result = _run_docker(
        runner,
        list(probe_argv(container)),
        deadline=deadline,
        step_id="pgadmin.master_password.probe",
    )
    if result.returncode != 0:
        raise PgAdminUnavailableError()
    token = result.stdout.strip()
    if token == "1":
        return True
    if token == "0":
        return False
    raise PgAdminUnavailableError()


def _selected_connection(preparation: PgAdminPreparation, database: str) -> tuple[str, int, str]:
    try:
        payload = json.loads(preparation.paths.servers_json.read_text(encoding="utf-8"))
        server = payload["Servers"]["1"]
    except (OSError, KeyError, TypeError, ValueError):
        raise PgAdminUnavailableError() from None
    if not isinstance(server, dict):
        raise PgAdminUnavailableError()
    host = server.get("Host")
    port = server.get("Port")
    user = server.get("Username")
    if (
        not isinstance(host, str)
        or not host
        or isinstance(port, bool)
        or not isinstance(port, int)
        or not isinstance(user, str)
        or not user
        or server.get("MaintenanceDB") != database
    ):
        raise PgAdminUnavailableError()
    return host, port, user


def _run_docker(
    runner: ComposeRunner, args: list[str], *, deadline: float, step_id: str
) -> subprocess.CompletedProcess[str]:
    from odoo_instance_sdk.internal.pgadmin_container import run_docker

    return run_docker(runner, args, deadline=deadline, step_id=step_id)
