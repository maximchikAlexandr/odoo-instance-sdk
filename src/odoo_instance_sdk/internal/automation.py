from __future__ import annotations

import base64
import contextlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.internal.address import AddressState, probe_address
from odoo_instance_sdk.internal.sanitize import sanitize_last_error
from odoo_instance_sdk.internal.server import parse_payload

if TYPE_CHECKING:
    from odoo_instance_sdk.resources.instance import OdooInstance

_MODULE_MANIFEST_RE = re.compile(r"^\s*(?:\{|['\"]info['\"]\s*[:=]\s*\{)", re.MULTILINE)


@dataclass(slots=True)
class ShellOutcome:
    returncode: int
    stdout: str
    stderr: str
    payload: dict[str, Any] | None


def _safe_stderr(value: str) -> str:
    return sanitize_last_error(value) or "<no diagnostic>"


def _run_with_payload(
    instance: OdooInstance,
    source: str,
    *,
    argv: tuple[str, ...] = (),
    commit: bool = False,
    timeout: float | None = None,
) -> ShellOutcome:
    result = instance.run_shell_script(source, argv=argv, timeout=timeout, commit=commit)
    payload = parse_payload(result.stdout)
    return ShellOutcome(
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        payload=payload,
    )


def eval_expression(
    instance: OdooInstance, expression: str, *, commit: bool = False
) -> ShellOutcome:
    if not isinstance(expression, str) or not expression.strip():
        raise ConfigError("eval requires a non-empty expression")
    source = f"result = ({expression})\n"
    return _run_with_payload(instance, source, commit=commit)


def exec_script(
    instance: OdooInstance, script: str, argv: tuple[str, ...] = (), *, commit: bool = False
) -> ShellOutcome:
    return _run_with_payload(instance, script, argv=argv, commit=commit)


@dataclass(slots=True)
class ModuleRecord:
    name: str
    state: str
    technical_name: str | None = None
    installed_version: str | None = None
    latest_version: str | None = None
    license: str | None = None
    summary: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state,
            "technical_name": self.technical_name,
            "installed_version": self.installed_version,
            "latest_version": self.latest_version,
            "license": self.license,
            "summary": self.summary,
        }


def list_modules(
    instance: OdooInstance,
    names: tuple[str, ...] = (),
    *,
    state: str | None = None,
) -> list[ModuleRecord]:
    names_repr = json.dumps(list(names))
    state_repr = json.dumps(state)
    source = (
        "import json as _mjson\n"
        f"_names = _mjson.loads({names_repr!r})\n"
        f"_state = _mjson.loads({state_repr!r})\n"
        "_dom = []\n"
        "if _names:\n"
        "    _dom.append(('name', 'in', _names))\n"
        "if _state:\n"
        "    _dom.append(('state', '=', _state))\n"
        "_mods = env['ir.module.module'].search(_dom, order='name')\n"
        "result = [\n"
        "    {'name': m.name, 'state': m.state, 'technical_name': m.name,\n"
        "     'installed_version': m.installed_version,\n"
        "     'latest_version': m.latest_version, 'license': m.license,\n"
        "     'summary': m.summary}\n"
        "    for m in _mods\n"
        "]\n"
    )
    outcome = _run_with_payload(instance, source)
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
                technical_name=item.get("technical_name"),
                installed_version=item.get("installed_version"),
                latest_version=item.get("latest_version"),
                license=item.get("license"),
                summary=item.get("summary"),
            )
        )
    return out


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
    modules_repr = json.dumps(list(plan.modules))
    source = (
        f"_mods = env['ir.module.module'].search([('name', 'in', {modules_repr!r}), "
        "('state', '=', 'installed')])\n"
        "_mods.button_immediate_upgrade()\n"
        "result = {'updated': list(_mods.mapped('name'))}\n"
    )
    _ = env_id
    result = instance._run_shell_script_exclusive(source, commit=True)
    return ShellOutcome(
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        payload=parse_payload(result.stdout),
    )


@dataclass(slots=True)
class TestRunResult:
    tests_count: int
    tests_success: int
    tests_errors: int
    tests_failed: int
    skipped: int
    had_failures: bool
    had_zero_tests: bool


def run_module_tests(
    instance: OdooInstance,
    modules: tuple[str, ...],
    test_tags: str,
    *,
    reload_tests: bool = False,
    allow_empty: bool = False,
    env_id: str,
    http_interface: str,
    http_port: int,
) -> tuple[TestRunResult, int]:
    if not modules:
        raise ConfigError("module test requires at least one module")
    if not test_tags:
        raise ConfigError("module test requires --test-tags")
    address_state = probe_address(http_interface, http_port)
    if address_state is not AddressState.FREE:
        raise ConfigError(
            f"port {address_state}: {http_interface}:{http_port} cannot be reserved for module tests"
        )

    modules_repr = json.dumps(list(modules))
    tags_repr = json.dumps(test_tags)
    reload_repr = json.dumps(reload_tests)
    source = (
        "from odoo.tests.shell import run_tests as _odcli_run_tests\n"
        f"_r = _odcli_run_tests(env, test_tags={tags_repr!r}, "
        f"modules={modules_repr!r}, reload_tests={reload_repr!r})\n"
        "result = {\n"
        "    'tests_count': getattr(_r, 'tests_count', 0),\n"
        "    'tests_success': getattr(_r, 'tests_success', 0),\n"
        "    'tests_errors': getattr(_r, 'tests_errors', 0),\n"
        "    'tests_failed': getattr(_r, 'tests_failed', 0),\n"
        "    'skipped': getattr(_r, 'skipped', 0),\n"
        "    'had_failures': bool(getattr(_r, 'tests_failed', 0) or getattr(_r, 'tests_errors', 0)),\n"
        "    'had_zero_tests': not bool(getattr(_r, 'tests_count', 0)),\n"
        "}\n"
    )
    _ = env_id
    result = instance._run_shell_script_exclusive(source)
    outcome = ShellOutcome(
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        payload=parse_payload(result.stdout),
    )
    if outcome.returncode != 0:
        raise RuntimeError(
            f"module test shell failed (rc={outcome.returncode}): {_safe_stderr(outcome.stderr)}"
        )
    payload = outcome.payload or {}
    raw = payload.get("result", {})
    if not isinstance(raw, dict):
        raw = {}
    res = TestRunResult(
        tests_count=int(raw.get("tests_count", 0)),
        tests_success=int(raw.get("tests_success", 0)),
        tests_errors=int(raw.get("tests_errors", 0)),
        tests_failed=int(raw.get("tests_failed", 0)),
        skipped=int(raw.get("skipped", 0)),
        had_failures=bool(raw.get("had_failures", False)),
        had_zero_tests=bool(raw.get("had_zero_tests", True)),
    )
    exit_code = 1 if res.had_failures or (res.had_zero_tests and not allow_empty) else 0
    return res, exit_code


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


def _expected_translation_filename(module: str, lang: str, raw: dict[str, Any]) -> str:
    if lang in ("pot", "__new__", ""):
        return f"{module}.pot"
    iso = raw.get("iso")
    if not isinstance(iso, str) or not iso or Path(iso).name != iso:
        raise ConfigError("translations export produced an invalid language code")
    return f"{iso}.po"


def _decode_translation_payload(data_b64: object, module: str, lang: str) -> bytes:
    if not isinstance(data_b64, str) or not data_b64:
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
    if not modules:
        raise ConfigError("translations export requires --module")
    if not languages:
        raise ConfigError("translations export requires --language")
    results: list[TranslationExportResult] = []
    for module in modules:
        for lang in languages:
            results.append(_export_one(instance, module, lang, worktree_root=worktree_root))
    return results


def _export_one(
    instance: OdooInstance,
    module: str,
    lang: str,
    *,
    worktree_root: Path,
) -> TranslationExportResult:
    if not module:
        raise ConfigError("translations export requires a module name")
    source = _build_export_source(module, lang)
    outcome = _run_with_payload(instance, source)
    if outcome.returncode != 0:
        raise RuntimeError(
            f"translations export failed (rc={outcome.returncode}): {_safe_stderr(outcome.stderr)}"
        )
    if outcome.payload is None or "result" not in outcome.payload:
        raise RuntimeError("translations export produced no payload")
    raw = outcome.payload.get("result")
    if not isinstance(raw, dict):
        raise TypeError("translations export produced malformed payload")
    if raw.get("error"):
        raise ConfigError(f"translations export error: {raw.get('error')}")
    addons_paths = (
        instance.config.start_config.addons_path
        if instance.config.start_config is not None
        else None
    )
    return _finalize_export(
        module, lang, raw, worktree_root=worktree_root, addons_paths=addons_paths
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
    raw: dict[str, Any],
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
    content = _decode_translation_payload(raw.get("data"), module, lang)
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
    distributions: list[dict[str, Any]] = field(default_factory=list)
    missing_imports: list[dict[str, str]] = field(default_factory=list)
    pip_check_ok: bool = True
    pip_check_output: str = ""


def verify_deps(
    *,
    recorded_python: Path,
    worktree_root: Path,
    uv_executable: str = "uv",
) -> DepsVerifyResult:
    result = DepsVerifyResult()
    pip_cmd = [uv_executable, "pip", "check", "--python", str(recorded_python)]
    proc = subprocess.run(pip_cmd, shell=False, capture_output=True, text=True, check=False)
    result.pip_check_output = (proc.stdout + proc.stderr).strip()
    result.pip_check_ok = proc.returncode == 0
    for raw_line in result.pip_check_output.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        result.distributions.append({"detail": stripped})
    declared = _scan_external_python_deps(worktree_root)
    for module_name, import_name in declared:
        check = subprocess.run(
            [str(recorded_python), "-c", f"import {import_name}"],
            shell=False,
            capture_output=True,
            text=True,
            check=False,
            cwd=str(worktree_root),
        )
        if check.returncode != 0:
            result.missing_imports.append({"module": module_name, "import": import_name})
    return result


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
