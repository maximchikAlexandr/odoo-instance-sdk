from __future__ import annotations

import contextlib
import os
import shutil
import stat
import tempfile
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

import msgspec

from odoo_instance_sdk.exceptions import InstanceConfigurationError
from odoo_instance_sdk.internal.proc import (
    PreparedAction,
    ProcessResult,
    RunContext,
    SubprocessExecutor,
)
from odoo_instance_sdk.models import BrowserLoginUser, CommandResult
from odoo_instance_sdk.resources.instance.auxiliary_restore import (
    _build_shell_script_step,
    _command_plan,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import Command
    from odoo_instance_sdk.resources.instance import OdooInstance


@dataclass(frozen=True, slots=True, repr=False)
class _BrowserSessionCredential:
    sid: str

    def __repr__(self) -> str:
        return "_BrowserSessionCredential(sid=<redacted>)"


def list_browser_login_users_command(
    instance: OdooInstance, query: str = ""
) -> Command[tuple[BrowserLoginUser, ...]]:
    """Return a bounded list of active internal non-superuser Odoo users."""
    if len(query) > 128:
        raise ValueError("browser login search is too long")
    source = """import json, sys
query = sys.argv[1].strip()
superuser_id = odoo.api.SUPERUSER_ID
domain = [('active', '=', True), ('share', '=', False), ('id', '!=', superuser_id)]
if query:
    domain = ['&', '&', '&', ('active', '=', True), ('share', '=', False), ('id', '!=', superuser_id), '|', ('login', 'ilike', query), ('name', 'ilike', query)]
users = env['res.users'].sudo().search(domain, limit=100, order='id')
print(json.dumps([{'id': user.id, 'login': user.login, 'name': user.name} for user in users[:100]]))
"""

    def decode(result: CommandResult) -> tuple[BrowserLoginUser, ...]:
        if result.returncode != 0:
            raise RuntimeError("browser login user query failed")
        try:
            return msgspec.json.decode(result.stdout, type=tuple[BrowserLoginUser, ...])
        except (msgspec.DecodeError, msgspec.ValidationError) as exc:
            raise RuntimeError("browser login user query returned invalid data") from exc

    return instance._shell_script_command(
        source,
        argv=(query,),
        timeout=30,
        commit=False,
        exclusive=False,
        result_converter=decode,
    )


def create_browser_session_command(  # noqa: C901
    instance: OdooInstance, user_id: int
) -> Command[_BrowserSessionCredential]:
    """Create an Odoo session and transfer its SID through a private file."""
    if type(user_id) is not int or user_id <= 0:
        raise ValueError("browser login user id must be positive")
    config = instance.config.start_config
    if config is None:
        raise InstanceConfigurationError(
            "No StartConfig — create instance via from_config() or from_environment()"
        )

    handoff_dir = Path(tempfile.gettempdir()) / f"odcli-browser-session-{uuid.uuid4().hex}"
    handoff_file = handoff_dir / "sid"
    source = """import os, sys
from odoo.http import root
user = env['res.users'].sudo().browse(int(sys.argv[2])).exists()
if not user or not user.active or user.share or user.id == odoo.api.SUPERUSER_ID:
    raise RuntimeError('selected user is not eligible')
store = root.session_store
if type(store).__name__ != 'FilesystemSessionStore':
    raise RuntimeError('unsupported session store')
session = store.new()
try:
    session.update({
        'db': env.cr.dbname,
        'login': user.login,
        'uid': user.id,
        'context': dict(user.context_get()),
        'session_token': user._compute_session_token(session.sid),
    })
    store.save(session)
    directory = os.path.dirname(os.path.abspath(sys.argv[1]))
    os.mkdir(directory, 0o700)
    descriptor = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as output:
        output.write(session.sid)
except Exception:
    store.delete(session)
    raise
finally:
    if os.path.exists(sys.argv[1]):
        os.chmod(sys.argv[1], 0o600)
"""
    cleanup_source = """import os, stat, sys
from odoo.http import root
path = sys.argv[1]
expected_uid = int(sys.argv[2])
try:
    directory_info = os.stat(os.path.dirname(path), follow_symlinks=False)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISDIR(directory_info.st_mode) or directory_info.st_mode & 0o077
                or directory_info.st_uid != os.geteuid() or not stat.S_ISREG(info.st_mode)
                or info.st_mode & 0o077 or info.st_uid != os.geteuid()):
            raise RuntimeError('unsafe handoff file')
        sid = os.read(descriptor, 512).decode('ascii')
    finally:
        os.close(descriptor)
    store = root.session_store
    if type(store).__name__ == 'FilesystemSessionStore' and sid:
        session = store.get(sid)
        if session and session.get('db') == env.cr.dbname and session.get('uid') == expected_uid:
            store.delete(session)
except Exception:
    pass
"""

    create_step, snapshot, secret_path, secrets = _build_shell_script_step(
        config,
        executable_prefix=instance._executable_prefix(),
        default_cwd=instance.config.default_cwd,
        source=source,
        argv=(str(handoff_file), str(user_id)),
        timeout=30,
        commit=True,
        project_environment=instance.config.project_environment,
    )
    create_step = replace(
        create_step,
        secret_values=tuple(dict.fromkeys((*create_step.secret_values, str(handoff_file)))),
    )
    cleanup_step, _, _, _ = _build_shell_script_step(
        snapshot,
        executable_prefix=instance._executable_prefix(),
        default_cwd=instance.config.default_cwd,
        source=cleanup_source,
        argv=(str(handoff_file), str(user_id)),
        timeout=15,
        commit=True,
        secret_config_path=secret_path,
        project_environment=instance.config.project_environment,
    )
    cleanup_step = replace(
        cleanup_step,
        step_id="instance.browser_session.cleanup",
        secret_values=tuple(dict.fromkeys((*cleanup_step.secret_values, str(handoff_file)))),
    )
    dependency_steps, dependency_temporary_path = instance._dependency_manifest()
    transaction = PreparedAction(
        step_id="instance.shell_script.transaction",
        action="commit",
        description="Commit the Odoo shell transaction",
        details={"commit": True},
        read_only=False,
        mutating=True,
    )
    steps = (*dependency_steps, create_step, cleanup_step, transaction)

    def read_private_handoff() -> str | None:
        try:
            descriptor = os.open(handoff_file, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                directory_info = os.stat(handoff_dir, follow_symlinks=False)
                info = os.fstat(descriptor)
                valid_file = (
                    stat.S_ISDIR(directory_info.st_mode)
                    and not directory_info.st_mode & 0o077
                    and directory_info.st_uid == os.geteuid()
                    and stat.S_ISREG(info.st_mode)
                    and not info.st_mode & 0o077
                    and info.st_uid == os.geteuid()
                )
                sid = os.read(descriptor, 512).decode("ascii")
            finally:
                os.close(descriptor)
        except (OSError, UnicodeError):
            return None
        return sid if valid_file and sid and len(sid) <= 256 else None

    def run_creation(context: RunContext[_BrowserSessionCredential]) -> _BrowserSessionCredential:
        result = cast("ProcessResult", context.process(create_step.step_id))
        if result.returncode != 0:
            raise RuntimeError("browser login session creation failed")
        sid = read_private_handoff()
        if sid is None:
            raise RuntimeError("browser login handoff is invalid")
        return _BrowserSessionCredential(sid)

    def execute(context: RunContext[_BrowserSessionCredential]) -> _BrowserSessionCredential:
        if secret_path is not None:
            from odoo_instance_sdk.internal.server import _write_secret_config

            _write_secret_config(snapshot, secret_path)
        try:
            with instance._artifact_operation(exclusive=True):
                instance._ensure_dependencies_ready(
                    context,
                    dependency_steps=dependency_steps,
                    temporary_path=dependency_temporary_path,
                )
                context.action(transaction.step_id)
                try:
                    credential = run_creation(context)
                except Exception as exc:
                    with contextlib.suppress(Exception):
                        context.process_prepared(cleanup_step)
                    message = str(exc)
                    sid = read_private_handoff()
                    if str(handoff_file) in message or (sid is not None and sid in message):
                        if isinstance(exc, TimeoutError):
                            raise TimeoutError("browser login session creation timed out") from None
                        raise RuntimeError("browser login session creation failed") from None
                    raise
                else:
                    context.skip(cleanup_step.step_id)
                    context.complete_action(transaction.step_id)
                    return credential
        finally:
            if secret_path is not None:
                from odoo_instance_sdk.internal.server import cleanup_secret_config

                cleanup_secret_config(secret_path)
            shutil.rmtree(handoff_dir, ignore_errors=True)

    from odoo_instance_sdk.execution import Command

    return Command.create(
        _command_plan(steps, secrets=(*secrets, str(handoff_file))),
        execute,
        steps,
        executor=SubprocessExecutor(),
    )
