from __future__ import annotations

import base64
import contextlib
import json
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar, cast

from odoo_instance_sdk.exceptions import ConfigError, StalePlanError
from odoo_instance_sdk.internal.address import AddressState, probe_address
from odoo_instance_sdk.internal.process_env import captured_child_environment
from odoo_instance_sdk.internal.sanitize import sanitize_last_error
from odoo_instance_sdk.internal.server import parse_payload
from odoo_instance_sdk.models import CommandResult, OdooTestResult, OdooTestSpec

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import Command, JsonValue
    from odoo_instance_sdk.internal.proc import PreparedStep, ProcessResult, RunContext
    from odoo_instance_sdk.resources.instance import OdooInstance


_MODULE_MANIFEST_RE = re.compile(r"^\s*(?:\{|['\"]info['\"]\s*[:=]\s*\{)", re.MULTILINE)
_PreflightT = TypeVar("_PreflightT")


@dataclass(slots=True)
class ShellOutcome:
    returncode: int
    stdout: str
    stderr: str
    payload: dict[str, JsonValue] | None


@dataclass(frozen=True, slots=True)
class TestCommandSnapshot:
    """Immutable selection/provenance facts captured before test execution."""

    worktree: Path | None
    git_head: str | None
    git_base: str | None
    changed_files: tuple[str, ...]
    modules: tuple[str, ...]
    database_names: tuple[str, ...]
    database_identity: tuple[str | None, int | None, str | None]
    interface: str
    port: int


def _test_command_preflight(  # noqa: C901
    snapshot: TestCommandSnapshot,
    instance: OdooInstance,
    spec: OdooTestSpec,
) -> Callable[[RunContext[_PreflightT]], None]:
    """Return the no-mutation validation for a captured Odoo test command."""

    def validate(context: RunContext[_PreflightT]) -> None:  # noqa: C901
        config = instance.config
        current_start = config.start_config
        current_interface = (
            current_start.http_interface if current_start is not None else snapshot.interface
        )
        current_port = current_start.http_port if current_start is not None else snapshot.port
        if current_interface != snapshot.interface or current_port != snapshot.port:
            raise StalePlanError(
                "captured test port/interface changed",
                expected=f"{snapshot.interface}:{snapshot.port}",
                actual=f"{current_interface}:{current_port}",
            )
        address_state = probe_address(snapshot.interface, snapshot.port)
        if address_state is not AddressState.FREE:
            raise StalePlanError(
                "captured test port state changed",
                expected="free",
                actual=str(address_state),
            )
        current_databases = tuple(config.configured_database_names)
        if current_databases != snapshot.database_names:
            raise StalePlanError(
                "captured test database identity changed",
                expected=list(snapshot.database_names),
                actual=list(current_databases),
            )
        current_identity = (config.db_host, config.db_port, config.db_user)
        if current_identity != snapshot.database_identity:
            raise StalePlanError(
                "captured test database connection identity changed",
                expected=list(snapshot.database_identity),
                actual=list(current_identity),
            )
        if tuple(spec.modules) != snapshot.modules:
            raise StalePlanError(
                "captured test module selection changed",
                expected=list(snapshot.modules),
                actual=list(spec.modules),
            )

        if snapshot.worktree is not None and snapshot.git_head is not None:
            captured = cast("ProcessResult", context.process("odoo.tests.provenance.git"))
            actual = "" if captured.stdout is None else str(captured.stdout).strip()
            if captured.returncode != 0 or actual != snapshot.git_head:
                raise StalePlanError(
                    "captured test Git revision changed",
                    expected=snapshot.git_head,
                    actual=actual,
                )
            if snapshot.changed_files and snapshot.git_base is not None:
                selection_result = cast(
                    "ProcessResult", context.process("odoo.tests.provenance.selection")
                )
                selected = tuple(
                    sorted(
                        line.strip()
                        for line in str(selection_result.stdout or "").splitlines()
                        if line.strip()
                    )
                )
                expected = tuple(sorted(snapshot.changed_files))
                if selection_result.returncode != 0 or selected != expected:
                    raise StalePlanError(
                        "captured changed-file selection changed",
                        expected=list(expected),
                        actual=list(selected),
                    )
        if snapshot.database_names and len(snapshot.database_names) == 1:
            modules_result = cast("ProcessResult", context.process("odoo.tests.provenance.modules"))
            module_output = "" if modules_result.stdout is None else str(modules_result.stdout)
            installed = tuple(
                sorted(line.strip() for line in module_output.splitlines() if line.strip())
            )
            if modules_result.returncode != 0 or installed != tuple(sorted(snapshot.modules)):
                raise StalePlanError(
                    "captured installed-module selection changed",
                    expected=list(snapshot.modules),
                    actual=list(installed),
                )

    return validate


def _safe_stderr(value: str) -> str:
    return sanitize_last_error(value) or "<no diagnostic>"


def eval_expression(
    instance: OdooInstance, expression: str, *, commit: bool = False
) -> ShellOutcome:
    return _shell_outcome(eval_expression_command(instance, expression, commit=commit).run())


def eval_expression_command(
    instance: OdooInstance, expression: str, *, commit: bool = False
) -> Command[CommandResult]:
    """Capture the exact Odoo shell used by the eval leaf."""
    if not isinstance(expression, str) or not expression.strip():
        raise ConfigError("eval requires a non-empty expression")
    return instance.run_shell_script_command(f"result = ({expression})\n", commit=commit)


def exec_script(
    instance: OdooInstance, script: str, argv: tuple[str, ...] = (), *, commit: bool = False
) -> ShellOutcome:
    return _shell_outcome(exec_script_command(instance, script, argv=argv, commit=commit).run())


def exec_script_command(
    instance: OdooInstance,
    script: str,
    argv: tuple[str, ...] = (),
    *,
    commit: bool = False,
) -> Command[CommandResult]:
    """Capture the exact Odoo shell used by the exec leaf."""
    return instance.run_shell_script_command(script, argv=argv, commit=commit)


def _shell_outcome(result: CommandResult) -> ShellOutcome:
    return ShellOutcome(
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        payload=parse_payload(result.stdout),
    )


@dataclass(slots=True)
class ModuleRecord:
    name: str
    state: str
    technical_name: str | None = None
    installed_version: str | None = None
    latest_version: str | None = None
    license: str | None = None
    summary: str | None = None

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "name": self.name,
            "state": self.state,
            "technical_name": self.technical_name,
            "installed_version": self.installed_version,
            "latest_version": self.latest_version,
            "license": self.license,
            "summary": self.summary,
        }


def _optional_text(value: JsonValue) -> str | None:
    return value if isinstance(value, str) else None


def list_modules(
    instance: OdooInstance,
    names: tuple[str, ...] = (),
    *,
    state: str | None = None,
) -> list[ModuleRecord]:
    outcome = _shell_outcome(list_modules_command(instance, names=names, state=state).run())
    if outcome.returncode != 0:
        raise RuntimeError(
            f"module list failed (rc={outcome.returncode}): {_safe_stderr(outcome.stderr)}"
        )
    if outcome.payload is None or "result" not in outcome.payload:
        return []
    raw = outcome.payload.get("result")
    if not isinstance(raw, list):
        return []
    out: list[ModuleRecord] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        out.append(
            ModuleRecord(
                name=str(item.get("name", "")),
                state=str(item.get("state", "")),
                technical_name=_optional_text(item.get("technical_name")),
                installed_version=_optional_text(item.get("installed_version")),
                latest_version=_optional_text(item.get("latest_version")),
                license=_optional_text(item.get("license")),
                summary=_optional_text(item.get("summary")),
            )
        )
    return out


def _module_list_source(names: tuple[str, ...], state: str | None) -> str:
    names_repr = json.dumps(list(names))
    state_repr = json.dumps(state)
    return (
        "import json as _mjson\n"
        f"_names = _mjson.loads({names_repr!r})\n"
        f"_state = _mjson.loads({state_repr!r})\n"
        "_dom = []\n"
        "if _names:\n"
        "    _dom.append(('name', 'in', _names))\n"
        "if _state:\n"
        "    _dom.append(('state', '=', _state))\n"
        "_mods = env['ir.module.module'].search(_dom, order='name')\n"
        "result = [{'name': m.name, 'state': m.state, 'technical_name': m.name, "
        "'installed_version': m.installed_version, 'latest_version': m.latest_version, "
        "'license': m.license, 'summary': m.summary} for m in _mods]\n"
    )


def list_modules_command(
    instance: OdooInstance,
    names: tuple[str, ...] = (),
    *,
    state: str | None = None,
) -> Command[CommandResult]:
    """Capture the read-only module listing as an inspectable child step."""
    source = _module_list_source(names, state)
    return instance.run_shell_script_command(source)


def module_records_from_result(result: CommandResult) -> list[ModuleRecord]:
    """Decode one captured module-list result without launching another child."""
    payload = parse_payload(result.stdout)
    raw = payload.get("result") if payload is not None else None
    if not isinstance(raw, list):
        return []
    records: list[ModuleRecord] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        records.append(
            ModuleRecord(
                name=str(item.get("name", "")),
                state=str(item.get("state", "")),
                technical_name=_optional_text(item.get("technical_name")),
                installed_version=_optional_text(item.get("installed_version")),
                latest_version=_optional_text(item.get("latest_version")),
                license=_optional_text(item.get("license")),
                summary=_optional_text(item.get("summary")),
            )
        )
    return records


@dataclass(slots=True)
class ModuleUpdatePlan:
    modules: list[str]
    not_installed: list[str] = field(default_factory=list)


def plan_module_update(instance: OdooInstance, modules: tuple[str, ...]) -> ModuleUpdatePlan:
    if not modules:
        raise ConfigError("module update requires at least one module")
    installed = list_modules(instance, names=modules, state="installed")
    installed_names = {m.name for m in installed}
    not_installed = [m for m in modules if m not in installed_names]
    return ModuleUpdatePlan(modules=list(installed_names), not_installed=not_installed)


def update_modules(
    instance: OdooInstance,
    modules: tuple[str, ...],
    *,
    env_id: str,
) -> ShellOutcome:
    plan = plan_module_update(instance, modules)
    if plan.not_installed:
        raise ConfigError(f"modules not installed: {', '.join(plan.not_installed)}")
    if not plan.modules:
        raise ConfigError("no installed modules to update")
    _ = env_id
    return _shell_outcome(
        update_modules_command(instance, tuple(plan.modules), env_id=env_id).run()
    )


def update_modules_command(
    instance: OdooInstance,
    modules: tuple[str, ...],
    *,
    env_id: str,
) -> Command[CommandResult]:
    """Capture one exact module-upgrade child after selection is frozen."""
    if not modules:
        raise ConfigError("no installed modules to update")
    source = _update_modules_source(modules)
    _ = env_id
    return instance._shell_script_command(source, commit=True, exclusive=True)


def _update_modules_source(modules: tuple[str, ...]) -> str:
    modules_repr = json.dumps(list(modules))
    return (
        f"_mods = env['ir.module.module'].search([('name', 'in', {modules_repr!r}), "
        "('state', '=', 'installed')])\n"
        "_mods.button_immediate_upgrade()\n"
        "result = {'updated': list(_mods.mapped('name'))}\n"
    )


def _test_runner_source(modules: tuple[str, ...], test_tags: str, reload_tests: bool) -> str:
    """Build the single native Odoo test invocation used by both APIs."""
    modules_repr = repr(modules)
    tags_repr = repr(test_tags)
    reload_repr = repr(reload_tests)
    return (
        "from odoo.tools import config as _odcli_config\n"
        "_odcli_config['workers'] = 0\n"
        "from odoo.tests.shell import run_tests as _odcli_run_tests\n"
        f"_r = _odcli_run_tests(env, test_tags={tags_repr}, "
        f"modules={modules_repr}, reload_tests={reload_repr})\n"
        "_tests = getattr(_r, 'testsRun', getattr(_r, 'tests_count', 0))\n"
        "_failed = getattr(_r, 'failures', getattr(_r, 'tests_failed', 0))\n"
        "_errors = getattr(_r, 'errors', getattr(_r, 'tests_errors', 0))\n"
        "_skipped = getattr(_r, 'skipped', 0)\n"
        "def _odcli_count(value):\n"
        "    if isinstance(value, int):\n"
        "        return max(value, 0)\n"
        "    try:\n"
        "        return len(value)\n"
        "    except TypeError:\n"
        "        return 0\n"
        "_tests = _odcli_count(_tests)\n"
        "_failed = _odcli_count(_failed)\n"
        "_errors = _odcli_count(_errors)\n"
        "_skipped = _odcli_count(_skipped)\n"
        "result = {'tests': _tests, 'successful': max(_tests - _failed - _errors - _skipped, 0), "
        "'failed': _failed, 'errors': _errors, 'skipped': _skipped}\n"
    )


def _nonnegative_count(value: JsonValue) -> int:
    return value if type(value) is int and value >= 0 else 0


def _test_counts(payload: dict[str, JsonValue]) -> dict[str, int]:
    raw = payload.get("result", {})
    if not isinstance(raw, dict):
        raw = {}
    return {
        key: _nonnegative_count(raw.get(key))
        for key in ("tests", "successful", "failed", "errors", "skipped")
    }


def run_odoo_tests(
    instance: OdooInstance,
    spec: OdooTestSpec,
    *,
    http_interface: str | None = None,
    http_port: int | None = None,
) -> tuple[OdooTestResult, str | None]:
    """Execute one validated native Odoo test plan under the bound lock."""
    return run_odoo_tests_command(
        instance,
        spec,
        http_interface=http_interface,
        http_port=http_port,
    ).run()


def module_tests_command(
    instance: OdooInstance,
    spec: OdooTestSpec,
    *,
    http_interface: str,
    http_port: int,
) -> Command[tuple[OdooTestResult, str | None]]:
    """Capture the same native test command used by the module-test leaf."""
    return run_odoo_tests_command(
        instance,
        spec,
        http_interface=http_interface,
        http_port=http_port,
    )


def run_odoo_tests_command(
    instance: OdooInstance,
    spec: OdooTestSpec,
    *,
    http_interface: str | None = None,
    http_port: int | None = None,
    selection_snapshot: TestCommandSnapshot | None = None,
) -> Command[tuple[OdooTestResult, str | None]]:
    """Capture the native Odoo test shell as one inspectable command."""
    if not isinstance(spec, OdooTestSpec):
        raise ConfigError("run_odoo_tests requires an OdooTestSpec")
    config = instance.config.start_config
    resolved_interface = http_interface or (
        config.http_interface if config is not None else "127.0.0.1"
    )
    resolved_port = http_port or (config.http_port if config is not None else 8069)
    address_state = probe_address(resolved_interface, resolved_port)
    if address_state is not AddressState.FREE:
        raise ConfigError(
            f"port {address_state}: {resolved_interface}:{resolved_port} cannot be reserved for module tests"
        )

    def convert(result: CommandResult) -> tuple[OdooTestResult, str | None]:
        payload = parse_payload(result.stdout)
        counts = _test_counts(payload or {})
        failures = counts["failed"] > 0 or counts["errors"] > 0
        zero_tests = counts["tests"] == 0
        exit_code = (
            1 if result.returncode != 0 or failures or (zero_tests and not spec.allow_empty) else 0
        )
        diagnostic = _safe_stderr(result.stderr) if result.returncode != 0 else None
        if result.returncode == 0 and failures and result.stderr:
            diagnostic = _safe_stderr(result.stderr)
        return (
            OdooTestResult(
                counts=counts,
                failures=failures,
                zero_tests=zero_tests,
                exit_code=exit_code,
            ),
            diagnostic,
        )

    from odoo_instance_sdk.internal.proc import PreparedStep

    captured_snapshot = selection_snapshot or TestCommandSnapshot(
        worktree=None,
        git_head=None,
        git_base=None,
        changed_files=(),
        modules=tuple(spec.modules),
        database_names=tuple(instance.config.configured_database_names),
        database_identity=(
            instance.config.db_host,
            instance.config.db_port,
            instance.config.db_user,
        ),
        interface=resolved_interface,
        port=resolved_port,
    )
    provenance_steps: list[PreparedStep] = []
    if captured_snapshot.worktree is not None and captured_snapshot.git_head is not None:
        provenance_steps.append(
            PreparedStep(
                step_id="odoo.tests.provenance.git",
                argv=(
                    "git",
                    "-C",
                    str(captured_snapshot.worktree),
                    "rev-parse",
                    "--verify",
                    "HEAD",
                ),
                cwd=str(captured_snapshot.worktree),
                read_only=True,
                text=True,
            )
        )
        if captured_snapshot.changed_files and captured_snapshot.git_base is not None:
            provenance_steps.append(
                PreparedStep(
                    step_id="odoo.tests.provenance.selection",
                    argv=(
                        "git",
                        "-C",
                        str(captured_snapshot.worktree),
                        "diff",
                        "--name-only",
                        f"{captured_snapshot.git_base}..HEAD",
                        "--",
                    ),
                    cwd=str(captured_snapshot.worktree),
                    read_only=True,
                    text=True,
                )
            )
    if captured_snapshot.database_names and len(captured_snapshot.database_names) == 1:
        database = captured_snapshot.database_names[0]
        query = (
            "SELECT name FROM ir_module_module WHERE state = 'installed' AND name IN ("
            + ", ".join(
                f"'{module.replace(chr(39), chr(39) + chr(39))}'" for module in spec.modules
            )
            + ") ORDER BY name"
        )
        instance_config = instance.config
        module_environment: tuple[tuple[str, str], ...] = ()
        if instance_config.db_password is not None:
            module_environment = (("PGPASSWORD", instance_config.db_password),)
        module_environment_snapshot, module_environment_overrides = captured_child_environment(
            dict(module_environment)
        )
        module_argv = ["psql", "-X", "-w"]
        if instance_config.db_host is not None:
            module_argv.extend(("-h", instance_config.db_host))
        module_argv.extend(
            [
                "-p",
                str(instance_config.db_port or 5432),
                "-U",
                instance_config.db_user or "",
                "-d",
                database,
                "-t",
                "-A",
                "-c",
                query,
            ]
        )
        provenance_steps.append(
            PreparedStep(
                step_id="odoo.tests.provenance.modules",
                argv=tuple(module_argv),
                environment=module_environment_overrides,
                environment_snapshot=module_environment_snapshot,
                environment_overrides=module_environment_overrides,
                secret_values=(instance_config.db_password,)
                if instance_config.db_password is not None
                else (),
                read_only=True,
                text=True,
            )
        )

    return instance._shell_script_command(
        _test_runner_source(spec.modules, spec.test_tags, spec.reload_tests),
        commit=False,
        exclusive=True,
        result_converter=convert,
        preflight=_test_command_preflight(captured_snapshot, instance, spec),
        extra_steps=tuple(provenance_steps),
    )


@dataclass(slots=True)
class TranslationExportResult:
    module: str
    requested_lang: str
    actual_filename: str
    path: Path
    bytes_written: int


def _is_path_within(child: Path, root: Path) -> bool:
    try:
        Path(child).resolve().relative_to(Path(root).resolve())
    except (ValueError, OSError):
        return False
    return True


def _resolve_worktree_module(
    worktree_root: Path, module: str, addons_paths: list[str] | None
) -> Path:
    """Resolve exactly one local addon directory without trusting Odoo output."""
    if not module or Path(module).name != module:
        raise ConfigError(f"invalid module name: {module!r}")
    root = worktree_root.resolve()
    candidates: list[Path] = []
    for addons_root in _addons_paths(worktree_root, addons_paths):
        try:
            addons_root.resolve().relative_to(root)
        except ValueError:
            continue
        candidate = addons_root / module
        if (
            candidate.is_dir()
            and not candidate.is_symlink()
            and (candidate / "__manifest__.py").is_file()
        ):
            candidates.append(candidate)
    unique = {path.resolve() for path in candidates}
    if len(unique) != 1:
        detail = "absent" if not unique else "ambiguous"
        raise ConfigError(f"module {module!r} is {detail} in worktree-local addons paths")
    return next(iter(unique))


def _addons_paths(worktree_root: Path, configured: list[str] | None) -> tuple[Path, ...]:
    """Keep only configured addon roots that resolve inside this worktree."""
    roots: list[Path] = []
    for raw_path in configured or []:
        path = Path(raw_path)
        roots.append(path if path.is_absolute() else worktree_root / path)
    return tuple(roots)


def _expected_translation_filename(module: str, lang: str, raw: dict[str, JsonValue]) -> str:
    if lang in ("pot", "__new__", ""):
        return f"{module}.pot"
    iso = raw.get("iso")
    if not isinstance(iso, str) or not iso or Path(iso).name != iso:
        raise ConfigError("translations export produced an invalid language code")
    return f"{iso}.po"


def _decode_translation_payload(data_b64: str, module: str, lang: str) -> bytes:
    if not data_b64:
        raise ConfigError(f"translations export produced empty payload for {module}/{lang}")
    try:
        content = base64.b64decode(data_b64, validate=True)
    except (ValueError, TypeError) as exc:
        raise ConfigError(
            f"translations export produced invalid base64 for {module}/{lang}"
        ) from exc
    if not content:
        raise ConfigError(f"translations export produced empty payload for {module}/{lang}")
    return content


def export_translations(
    instance: OdooInstance,
    modules: tuple[str, ...],
    languages: tuple[str, ...],
    *,
    worktree_root: Path,
) -> list[TranslationExportResult]:
    return export_translations_command(
        instance,
        modules,
        languages,
        worktree_root=worktree_root,
    ).run()


def export_translations_command(
    instance: OdooInstance,
    modules: tuple[str, ...],
    languages: tuple[str, ...],
    *,
    worktree_root: Path,
) -> Command[list[TranslationExportResult]]:
    """Capture all translation requests in one Odoo shell invocation."""
    if not modules or not languages:
        raise ConfigError("translations export requires --module and --language")
    chunks = [
        _build_export_source(module, language) for module in modules for language in languages
    ]
    source = "_odcli_exports = []\n"
    for index, chunk in enumerate(chunks):
        if index:
            source += "del result\n"
        source += chunk
        source += "_odcli_exports.append(result)\n"
    source += "result = _odcli_exports\n"

    def convert(result: CommandResult) -> list[TranslationExportResult]:
        payload = parse_payload(result.stdout)
        raw_results = payload.get("result") if payload is not None else None
        if not isinstance(raw_results, list):
            raise ConfigError("translations export produced no payload")
        addons_paths = (
            instance.config.start_config.addons_path
            if instance.config.start_config is not None
            else None
        )
        return [
            _finalize_export(
                module,
                language,
                raw if isinstance(raw, dict) else {},
                worktree_root=worktree_root,
                addons_paths=addons_paths,
            )
            for (module, language), raw in zip(
                ((m, lang) for m in modules for lang in languages), raw_results, strict=True
            )
        ]

    return instance._shell_script_command(
        source, commit=False, exclusive=False, result_converter=convert
    )


def _build_export_source(module: str, lang: str) -> str:
    modules_repr = json.dumps([module])
    lang_repr = json.dumps(lang)
    return (
        "import base64 as _b64\n"
        "from odoo.tools import get_iso_codes as _get_iso_codes\n"
        f"_module = {modules_repr!r}[0]\n"
        f"_lang = {lang_repr!r}\n"
        "_is_pot = _lang in ('pot', '__new__', '', None)\n"
        "if _is_pot:\n"
        "    _wiz = env['base.language.export'].create({\n"
        "        'name': '__new__', 'format': 'po', 'export_type': 'module',\n"
        f"        'modules': [(_module,)] if False else [(6, 0, env['ir.module.module'].search([('name','=',_module)]).ids)],\n"
        "    })\n"
        "else:\n"
        "    _lang_obj = env['res.lang'].search([('code', '=', _lang)], limit=1)\n"
        "    if not _lang_obj:\n"
        "        result = {'error': 'language not active', 'lang': _lang}\n"
        "    else:\n"
        "        _wiz = env['base.language.export'].create({\n"
        "            'name': _lang_obj.id, 'format': 'po', 'export_type': 'module',\n"
        f"            'modules': [(6, 0, env['ir.module.module'].search([('name','=',_module)]).ids)],\n"
        "        })\n"
        "if 'result' not in globals() or not isinstance(result, dict) or 'error' not in result:\n"
        "    _wiz.act_update()\n"
        "    _iso = _get_iso_codes(_wiz.name) if not _is_pot else _wiz.name\n"
        "    _data = _wiz.data or ''\n"
        "    _mod_obj = env['ir.module.module'].search([('name','=',_module)], limit=1)\n"
        "    _mod_path = _mod_obj._module_path if hasattr(_mod_obj, '_module_path') else None\n"
        "    result = {\n"
        "        'iso': _iso, 'filename': (_iso or _lang) + '.po',\n"
        "        'data': _data, 'module': _module,\n"
        "        'installed': bool(_mod_obj and _mod_obj.state in ('installed', 'to upgrade')),\n"
        "        'lang': _lang,\n"
        "    }\n"
    )


def _finalize_export(
    module: str,
    lang: str,
    raw: dict[str, JsonValue],
    *,
    worktree_root: Path,
    addons_paths: list[str] | None,
) -> TranslationExportResult:
    installed = raw.get("installed")
    if not installed:
        raise ConfigError(f"module {module!r} is not installed")
    filename = raw.get("filename")
    expected_filename = _expected_translation_filename(module, lang, raw)
    if (
        not isinstance(filename, str)
        or Path(filename).name != filename
        or filename != expected_filename
    ):
        raise ConfigError(f"translations export produced unexpected filename for {module}/{lang}")
    module_dir = _resolve_worktree_module(worktree_root, module, addons_paths)
    target_dir = module_dir / "i18n"
    target = target_dir / filename
    if not _is_path_within(target, worktree_root) or target_dir.is_symlink():
        raise ConfigError(f"target path {target} escapes worktree root {worktree_root}")
    data = raw.get("data")
    if not isinstance(data, str):
        raise ConfigError(f"translations export produced empty payload for {module}/{lang}")
    content = _decode_translation_payload(data, module, lang)
    target_dir.mkdir(parents=True, exist_ok=True)
    if target_dir.is_symlink() or not _is_path_within(target, worktree_root):
        raise ConfigError(f"target path {target} escapes worktree root {worktree_root}")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{filename}.", suffix=".tmp", dir=target_dir)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    try:
        if target.exists():
            with contextlib.suppress(OSError):
                tmp.chmod(target.stat().st_mode & 0o7777)
        tmp.replace(target)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    return TranslationExportResult(
        module=module,
        requested_lang=lang,
        actual_filename=filename,
        path=target,
        bytes_written=len(content),
    )


@dataclass(slots=True)
class DepsVerifyResult:
    distributions: list[dict[str, JsonValue]] = field(default_factory=list)
    missing_imports: list[dict[str, str]] = field(default_factory=list)
    pip_check_ok: bool = True
    pip_check_output: str = ""


def verify_deps(
    *,
    recorded_python: Path,
    worktree_root: Path,
    uv_executable: str = "uv",
) -> DepsVerifyResult:
    return verify_deps_command(
        recorded_python=recorded_python,
        worktree_root=worktree_root,
        uv_executable=uv_executable,
    ).run()


def verify_deps_command(
    *,
    recorded_python: Path,
    worktree_root: Path,
    uv_executable: str = "uv",
) -> Command[DepsVerifyResult]:
    """Capture dependency verification probes in one command ledger."""
    from odoo_instance_sdk.execution import Command, ExecutionPlan
    from odoo_instance_sdk.internal.proc import PreparedStep, SubprocessExecutor

    imports = _scan_external_python_deps(worktree_root)
    steps = [
        PreparedStep(
            step_id="deps.verify.pip-check",
            argv=(uv_executable, "pip", "check", "--python", str(recorded_python)),
            read_only=True,
            text=True,
        )
    ]
    for index, (_module, import_name) in enumerate(imports):
        steps.append(
            PreparedStep(
                step_id=f"deps.verify.import.{index}",
                argv=(str(recorded_python), "-c", f"import {import_name}"),
                cwd=str(worktree_root),
                read_only=True,
                text=True,
            )
        )

    def run(context: RunContext[DepsVerifyResult]) -> DepsVerifyResult:
        result = DepsVerifyResult()
        check = cast("ProcessResult", context.process(steps[0].step_id))
        stdout = check.stdout if isinstance(check.stdout, str) else ""
        stderr = check.stderr if isinstance(check.stderr, str) else ""
        result.pip_check_output = (stdout + stderr).strip()
        result.pip_check_ok = check.returncode == 0
        for raw_line in result.pip_check_output.splitlines():
            if raw_line.strip():
                result.distributions.append({"detail": raw_line.strip()})
        for index, (module_name, import_name) in enumerate(imports):
            probe = cast("ProcessResult", context.process(f"deps.verify.import.{index}"))
            if probe.returncode != 0:
                result.missing_imports.append({"module": module_name, "import": import_name})
        return result

    plan = ExecutionPlan(steps=tuple(step.public_projection() for step in steps))
    return Command.create(plan, run, tuple(steps), executor=SubprocessExecutor())


def _scan_external_python_deps(worktree_root: Path) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for manifest_path in worktree_root.glob("**/__manifest__.py"):
        module_name = manifest_path.parent.name
        try:
            text = manifest_path.read_text(encoding="utf-8")
        except OSError:
            continue
        deps = _extract_external_python(text)
        for import_name in deps:
            out.append((module_name, import_name))
    return out


def _extract_external_python(manifest_text: str) -> list[str]:
    if not _MODULE_MANIFEST_RE.match(manifest_text):
        return []
    m = re.search(
        r"['\"]external_dependencies['\"]\s*:\s*\{",
        manifest_text,
    )
    if m is None:
        return []
    start = m.end()
    py_m = re.search(r"['\"]python['\"]\s*:", manifest_text[start:])
    if py_m is None:
        return []
    py_start = start + py_m.end()
    depth = 0
    end = py_start
    for i, ch in enumerate(manifest_text[py_start:], start=py_start):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
        elif ch == "}" and depth == 0:
            end = i
            break
    slice_ = manifest_text[py_start:end]
    return re.findall(r"['\"]([A-Za-z_][A-Za-z0-9_.]*)['\"]", slice_)
