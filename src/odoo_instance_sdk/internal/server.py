from __future__ import annotations

import contextlib
import os
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, cast

from msgspec import structs

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import JsonValue
from odoo_instance_sdk.models import (
    CommandResult,
    ProcessStatus,
    StartConfig,
)

_SENSITIVE_FIELDS = frozenset({"db_password", "admin_passwd", "config_path", "logfile"})


def _cli_flag(field_name: str) -> str:
    odoo_spelling = {
        "db_name": "--database",
        "dbfilter": "--db-filter",
        "db_host": "--db_host",
        "db_port": "--db_port",
        "db_user": "--db_user",
    }
    if field_name in odoo_spelling:
        return odoo_spelling[field_name]
    if field_name == "dev_mode":
        return "--dev"
    return "--" + field_name.replace("_", "-")


def _build_cli_args(config: StartConfig, *, secret_config_path: str | None = None) -> list[str]:
    args: list[str] = []
    for f in structs.fields(StartConfig):
        if f.name in _SENSITIVE_FIELDS:
            continue
        value = getattr(config, f.name)
        if value is None:
            continue
        flag = _cli_flag(f.name)
        if isinstance(value, list):
            args.extend([flag, ",".join(value)])
        else:
            args.extend([flag, str(value)])
    if config.config_path is not None:
        args.extend(["--config", config.config_path])
    elif secret_config_path is not None:
        args.extend(["--config", secret_config_path])
    return args


def _write_secret_config(
    config: StartConfig, secret_config_path: str | Path | None = None
) -> str | None:
    db_password = getattr(config, "db_password", None)
    if db_password is None:
        return None
    if secret_config_path is None:
        fd, path = tempfile.mkstemp(suffix=".conf", prefix="odoo-sdk-")
    else:
        path = str(secret_config_path)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write("[options]\n")
            f.write(f"db_password = {db_password}\n")
        os.chmod(path, 0o600)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(path)
        raise
    return path


def cleanup_secret_config(secret_config_path: str | None) -> None:
    if secret_config_path is not None:
        with contextlib.suppress(OSError):
            os.unlink(secret_config_path)


def get_process_status(
    handle: subprocess.Popen[bytes] | None,
) -> ProcessStatus:
    if handle is None:
        return ProcessStatus(state="exited")
    rc = handle.poll()
    if rc is None:
        return ProcessStatus(state="running")
    return ProcessStatus(state="exited", returncode=rc)


def run_command(
    executable: str | Sequence[str],
    args: list[str],
    *,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> CommandResult:
    from odoo_instance_sdk.internal.proc import run_captured
    from odoo_instance_sdk.internal.proc.redaction import (
        captured_argv_secret_values,
        redacted_argv,
        redacted_environment,
        redacted_projection,
    )

    prefix = [executable] if isinstance(executable, str) else list(executable)
    full_args = [*prefix, *args]
    proc = run_captured(full_args, cwd=cwd, env=env, timeout=timeout, text=True)
    secrets = captured_argv_secret_values(full_args, secrets=(env or {}).values())
    return CommandResult(
        args=list(redacted_argv(full_args, secrets=secrets)),
        returncode=proc.returncode,
        stdout=(
            cast("str", redacted_projection(proc.stdout, secrets=secrets, field="stdout"))
            if isinstance(proc.stdout, str)
            else ""
        ),
        stderr=(
            cast("str", redacted_projection(proc.stderr, secrets=secrets, field="stderr"))
            if isinstance(proc.stderr, str)
            else ""
        ),
        duration=proc.duration,
        cwd=proc.cwd,
        environment=redacted_environment(proc.environment, secrets=secrets),
        timeout=timeout,
    )


def wait_foreground_process(proc: subprocess.Popen[bytes]) -> int:
    """Block until ``proc`` exits, terminating its owned group on Ctrl+C.

    Ctrl+C uses the same bounded TERM/KILL/reap cleanup as exceptional wait
    failures, then returns 130. Restores the previous SIGINT handler on return.
    """
    from odoo_instance_sdk.internal.proc import owned_handle, wait_foreground

    return wait_foreground(owned_handle(proc, process_group_id=proc.pid))


_RESULT_SNIPPET = (
    "import json as _odcli_rj\n"
    "def _odcli_serialize_result(_r):\n"
    "    if _r is None or isinstance(_r, (bool, int, float, str)):\n"
    "        return _r\n"
    "    if isinstance(_r, (list, tuple)):\n"
    "        try:\n"
    "            return [_odcli_serialize_result(_x) for _x in _r]\n"
    "        except Exception:\n"
    "            return _odcli_sanitize(_r)\n"
    "    if isinstance(_r, dict):\n"
    "        try:\n"
    "            return {str(_k): _odcli_serialize_result(_v) for _k, _v in _r.items()}\n"
    "        except Exception:\n"
    "            return _odcli_sanitize(_r)\n"
    "    if hasattr(_r, 'ids') and (hasattr(_r, '_name') or hasattr(_r, '_model')):\n"
    "        _name = getattr(_r, '_name', None)\n"
    "        if _name is None and hasattr(_r, '_model'):\n"
    "            _name = getattr(_r._model, '_name', None)\n"
    "        try:\n"
    "            return {'model': _name, 'ids': list(_r.ids), 'count': len(_r)}\n"
    "        except Exception:\n"
    "            return _odcli_sanitize(_r)\n"
    "    return _odcli_sanitize(_r)\n"
    "def _odcli_sanitize(_o, _max=500):\n"
    "    try:\n"
    "        _t = repr(_o)\n"
    "    except Exception:\n"
    "        _t = '<unrepresentable>'\n"
    "    _t = ' '.join(str(_t).split())\n"
    "    if len(_t) > _max:\n"
    "        _t = _t[:_max] + '...<truncated>'\n"
    "    for _kw in ('password', 'passwd', 'token', 'secret', 'api_key', 'apikey'):\n"
    "        if _kw in _t.lower():\n"
    "            _t = '<redacted>'\n"
    "            break\n"
    "    return _t\n"
)


def _build_shell_wrapper(source: str, argv: list[str], *, commit: bool, nonce: str) -> str:
    marker_open = f"__ODCLI_PAYLOAD__{nonce}__"
    marker_close = f"__END_PAYLOAD__{nonce}__"
    import json

    source_repr = json.dumps(source)
    argv_repr = json.dumps(argv)
    payload_dict = "{'ok': True, 'commit': " + repr(commit) + "}"
    result_emit = (
        "    if 'result' in globals() and result is not None:\n"
        "        _payload.update({'result': _odcli_serialize_result(result)})\n"
    )
    return (
        "import json as _json, sys as _sys\n"
        f"_sys.argv = [_sys.argv[0], *_json.loads({argv_repr!r})]\n"
        f"_source = _json.loads({source_repr!r})\n"
        f"{_RESULT_SNIPPET}"
        "try:\n"
        "    exec(compile(_source, '<odcli-shell-script>', 'exec'), globals())\n"
        "finally:\n"
        "    try:\n"
        "        if env is not None and hasattr(env, 'cr') and env.cr is not None:\n"
        f"            env.cr.commit() if {commit!r} else env.cr.rollback()\n"
        "    except Exception:\n"
        "        pass\n"
        f"    _payload = {payload_dict}\n"
        f"{result_emit}"
        f"    print({marker_open!r}, _json.dumps(_payload), {marker_close!r})\n"
    )


def parse_payload(stdout: str, nonce: str | None = None) -> dict[str, JsonValue] | None:
    import json as _json
    import re as _re

    if nonce is not None:
        marker_open = f"__ODCLI_PAYLOAD__{nonce}__"
        marker_close = f"__END_PAYLOAD__{nonce}__"
        start = stdout.rfind(marker_open)
        if start == -1:
            return None
        start += len(marker_open)
        end = stdout.rfind(marker_close, start)
        if end == -1:
            return None
        body = stdout[start:end].strip()
    else:
        match = None
        for m in _re.finditer(
            r"__ODCLI_PAYLOAD__([0-9a-fA-F]+)__\s*(.*?)\s*__END_PAYLOAD__\1__",
            stdout,
            _re.DOTALL,
        ):
            match = m
        if match is None:
            return None
        body = match.group(2).strip()
    if not body:
        return None
    try:
        return cast("dict[str, JsonValue]", _json.loads(body))
    except ValueError:
        return None
