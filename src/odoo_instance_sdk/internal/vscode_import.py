from __future__ import annotations

import contextlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import json5

from odoo_instance_sdk.exceptions import VscodeImportError
from odoo_instance_sdk.project import ProjectConfig

_WORKSPACE_FOLDER_RE = re.compile(r"\$\{workspaceFolder(:[^}]*)?\}")
_VAR_RE = re.compile(r"\$\{[^}]*\}")

_DROPPED_REPORT_ARGS = frozenset({"-u", "-i", "--stop-after-init", "--update", "--init"})
_CONFIG_OVERLAY_ARGS = {"--addons-path", "--upgrade-path"}


@dataclass(slots=True, kw_only=True)
class VscodeImportReport:
    ignored_pre_launch_task: bool = False
    ignored_env_file: bool = False
    ignored_inline_env: bool = False
    dropped_args: tuple[str, ...] = ()
    ignored_config_overlays: tuple[str, ...] = ()
    external_cwd_warning: bool = False
    source_file: str = ""
    source_profile: str = ""


@dataclass(slots=True, kw_only=True)
class VscodeImportResult:
    config: ProjectConfig
    provenance: dict[str, object]
    report: VscodeImportReport


def import_vscode_launch(
    launch_json_path: str | Path,
    *,
    launch_name: str | None = None,
    no_input: bool = False,
    select_profile: str | None = None,
) -> VscodeImportResult:
    path = Path(launch_json_path)
    if not path.is_file():
        raise VscodeImportError(f"VS Code launch file not found: {path}")
    try:
        raw = path.read_text(encoding="utf-8")
        data = json5.loads(raw)
    except ValueError as e:
        raise VscodeImportError(f"Failed to parse {path}: {e}") from e
    if not isinstance(data, dict):
        raise VscodeImportError(f"{path}: top-level must be a JSON object")
    configurations = data.get("configurations")
    if not isinstance(configurations, list):
        raise VscodeImportError(f"{path}: no 'configurations' list")

    candidates = _collect_odoo_candidates(configurations)
    if not candidates:
        raise VscodeImportError(f"{path}: no Odoo-like launch configurations found")

    chosen = _choose_candidate(candidates, launch_name=launch_name, no_input=no_input)
    if select_profile is not None and chosen["name"] != select_profile and launch_name is None:
        chosen = next((c for c in candidates if c["name"] == select_profile), chosen)

    workspace_dir = path.parent.parent if path.name == "launch.json" else path.parent
    report = VscodeImportReport(source_file=str(path), source_profile=str(chosen["name"]))
    config = _map_profile(chosen, workspace_dir=workspace_dir, report=report)

    provenance: dict[str, object] = {
        "vscode": {
            "file": str(path),
            "profile": chosen["name"],
            "external_cwd_warning": report.external_cwd_warning,
            "dropped_args": list(report.dropped_args),
            "ignored_config_overlays": list(report.ignored_config_overlays),
            "ignored_pre_launch_task": report.ignored_pre_launch_task,
            "ignored_env_file": report.ignored_env_file,
            "ignored_inline_env": report.ignored_inline_env,
        }
    }
    return VscodeImportResult(config=config, provenance=provenance, report=report)


def _collect_odoo_candidates(configurations: list[object]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for cfg in configurations:
        if not isinstance(cfg, dict):
            continue
        if cfg.get("type") not in ("python", "debugpy"):
            continue
        if cfg.get("request") != "launch":
            continue
        program = cfg.get("program")
        if not isinstance(program, str):
            continue
        if not _is_odoo_program(program):
            continue
        out.append(cfg)
    return out


def _is_odoo_program(program: str) -> bool:
    base = program.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return base in ("odoo-bin", "odoo", "odoo.py") or "odoo-bin" in program or "odoo.py" in program


def _choose_candidate(
    candidates: list[dict[str, object]],
    *,
    launch_name: str | None,
    no_input: bool,
) -> dict[str, object]:
    if launch_name is not None:
        for c in candidates:
            if c.get("name") == launch_name:
                return c
        names = [str(c.get("name", "?")) for c in candidates]
        raise VscodeImportError(
            f"launch profile {launch_name!r} not found; candidates: {', '.join(names)}"
        )
    if len(candidates) == 1:
        return candidates[0]
    names = [str(c.get("name", "?")) for c in candidates]
    if no_input:
        raise VscodeImportError(
            f"Multiple Odoo-like launch profiles found; pass --launch-name. Candidates: {', '.join(names)}"
        )
    raise VscodeImportError(
        f"Multiple Odoo-like launch profiles found; interactive selection not available in this call. "
        f"Candidates: {', '.join(names)}"
    )


def _map_profile(
    profile: dict[str, object],
    *,
    workspace_dir: Path,
    report: VscodeImportReport,
) -> ProjectConfig:
    odoo_bin = _resolve_program(str(profile.get("program", "")), workspace_dir=workspace_dir)
    python = _resolve_python(profile.get("python"), workspace_dir=workspace_dir)
    args = _ensure_list(profile.get("args"))
    parsed = _parse_args(args, workspace_dir=workspace_dir)
    runtime_cwd = _resolve_cwd(profile.get("cwd"), workspace_dir=workspace_dir, report=report)

    if profile.get("preLaunchTask") is not None:
        report.ignored_pre_launch_task = True
    if profile.get("envFile") is not None:
        report.ignored_env_file = True
    if profile.get("env") is not None:
        report.ignored_inline_env = True

    report.dropped_args = tuple(parsed.dropped)
    report.ignored_config_overlays = tuple(parsed.overlays)

    return ProjectConfig(
        odoo_bin=odoo_bin,
        python=python,
        source_config=parsed.source_config,
        default_source_database=parsed.default_source_database,
        preferred_http_port=parsed.preferred_http_port,
        requirements=(),
        default_run_args=tuple(parsed.run_args),
        runtime_cwd=runtime_cwd,
    )


@dataclass(slots=True, kw_only=True)
class _ParsedArgs:
    source_config: Path | None = None
    default_source_database: str | None = None
    preferred_http_port: int | None = None
    run_args: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    overlays: list[str] = field(default_factory=list)


def _parse_args(args: list[str], *, workspace_dir: Path) -> _ParsedArgs:
    parsed = _ParsedArgs()
    i = 0
    while i < len(args):
        arg = args[i]
        next_val = args[i + 1] if i + 1 < len(args) else None
        new_i = _handle_config(arg, next_val, parsed, workspace_dir=workspace_dir, i=i)
        if new_i is not None:
            i = new_i
            continue
        new_i = _handle_db(arg, next_val, parsed, i=i)
        if new_i is not None:
            i = new_i
            continue
        new_i = _handle_port(arg, next_val, parsed, i=i)
        if new_i is not None:
            i = new_i
            continue
        new_i = _handle_dev(arg, next_val, parsed, i=i)
        if new_i is not None:
            i = new_i
            continue
        new_i = _handle_dropped(arg, next_val, parsed, i=i)
        if new_i is not None:
            i = new_i
            continue
        new_i = _handle_overlay(arg, next_val, parsed, i=i)
        if new_i is not None:
            i = new_i
            continue
        i += 1
    return parsed


def _handle_config(
    arg: str, next_val: str | None, parsed: _ParsedArgs, *, workspace_dir: Path, i: int
) -> int | None:
    if arg in ("-c", "--config") and next_val is not None:
        parsed.source_config = _resolve_path_arg(next_val, workspace_dir=workspace_dir)
        return i + 2
    if arg.startswith("-c") and len(arg) > 2:
        parsed.source_config = _resolve_path_arg(arg[2:], workspace_dir=workspace_dir)
        return i + 1
    return None


def _handle_db(arg: str, next_val: str | None, parsed: _ParsedArgs, *, i: int) -> int | None:
    if arg in ("-d", "--database") and next_val is not None:
        parsed.default_source_database = str(next_val)
        return i + 2
    if arg.startswith("-d") and len(arg) > 2:
        parsed.default_source_database = arg[2:]
        return i + 1
    return None


def _handle_port(arg: str, next_val: str | None, parsed: _ParsedArgs, *, i: int) -> int | None:
    if arg == "--http-port" and next_val is not None:
        with contextlib.suppress(ValueError):
            parsed.preferred_http_port = int(next_val)
        return i + 2
    if arg.startswith("--http-port="):
        with contextlib.suppress(ValueError):
            parsed.preferred_http_port = int(arg.split("=", 1)[1])
        return i + 1
    return None


def _handle_dev(arg: str, next_val: str | None, parsed: _ParsedArgs, *, i: int) -> int | None:
    if arg == "--dev" and next_val is not None:
        parsed.run_args.append(f"--dev={next_val}")
        return i + 2
    if arg.startswith("--dev="):
        parsed.run_args.append(arg)
        return i + 1
    return None


def _handle_dropped(arg: str, next_val: str | None, parsed: _ParsedArgs, *, i: int) -> int | None:
    if arg not in _DROPPED_REPORT_ARGS:
        return None
    value = ""
    if arg in ("-u", "-i") and next_val is not None:
        value = f" {next_val}"
        return_idx = i + 2
    else:
        return_idx = i + 1
    parsed.dropped.append(f"{arg}{value}")
    return return_idx


def _handle_overlay(arg: str, next_val: str | None, parsed: _ParsedArgs, *, i: int) -> int | None:
    if arg in _CONFIG_OVERLAY_ARGS and next_val is not None:
        parsed.overlays.append(f"{arg} {next_val}")
        return i + 2
    if arg.startswith(("--addons-path=", "--upgrade-path=")):
        parsed.overlays.append(arg)
        return i + 1
    return None


def _ensure_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [str(value)]


def _resolve_program(program: str, *, workspace_dir: Path) -> Path | None:
    if not program:
        return None
    expanded = _expand_static_var(program, workspace_dir=workspace_dir)
    return Path(expanded)


def _resolve_python(value: object, *, workspace_dir: Path) -> str | Path | None:
    if value is None:
        return None
    s = str(value)
    expanded = _expand_static_var(s, workspace_dir=workspace_dir)
    if expanded.startswith(("/", "./")) or (len(expanded) > 1 and expanded[1] == ":"):
        return Path(expanded)
    return expanded


def _resolve_path_arg(value: str, *, workspace_dir: Path) -> Path | None:
    if not value:
        return None
    expanded = _expand_static_var(value, workspace_dir=workspace_dir)
    return Path(expanded)


def _resolve_cwd(value: object, *, workspace_dir: Path, report: VscodeImportReport) -> Path | None:
    if value is None:
        return None
    s = str(value)
    expanded = _expand_static_var(s, workspace_dir=workspace_dir)
    p = Path(expanded)
    if p.is_absolute():
        try:
            return p.relative_to(workspace_dir.resolve())
        except ValueError:
            report.external_cwd_warning = True
            return p
    return p


def _expand_static_var(value: str, *, workspace_dir: Path) -> str:
    if "${workspaceFolder" in value:
        return _WORKSPACE_FOLDER_RE.sub(_make_workspace_replacer(workspace_dir, value), value)
    if _VAR_RE.search(value):
        raise VscodeImportError(
            f"Unsupported variable in {value!r}: only static ${{workspaceFolder}} is allowed"
        )
    return value


def _make_workspace_replacer(workspace_dir: Path, original: str) -> Callable[[re.Match[str]], str]:
    def _repl(m: re.Match[str]) -> str:
        suffix = m.group(1)
        if suffix:
            raise VscodeImportError(
                f"Unsupported workspace variable {original!r}: named-workspace folders not supported"
            )
        return str(workspace_dir)

    return _repl
