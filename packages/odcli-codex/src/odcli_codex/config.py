"""Extension settings stored in the existing project manifest."""

from __future__ import annotations

import tomllib
from pathlib import Path

import msgspec

from odcli_codex.errors import CodexWorkflowError

_DEFAULT_LIMIT = 262144


class CodexSettings(msgspec.Struct, frozen=True, kw_only=True):
    """Effective `[codex]` values for one project."""

    model: str | None
    max_context_bytes: int


def load_settings(project_root: Path) -> CodexSettings:
    """Read `[codex]` from `.odcli/project.toml` when that table is present."""
    manifest = project_root / ".odcli" / "project.toml"
    if not manifest.is_file():
        return CodexSettings(model=None, max_context_bytes=_DEFAULT_LIMIT)
    data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    section = data.get("codex", {})
    if section is None:
        section = {}
    if not isinstance(section, dict):
        raise CodexWorkflowError("[codex] must be a table in .odcli/project.toml")
    model = section.get("model")
    if model is not None and not isinstance(model, str):
        raise CodexWorkflowError("[codex].model must be a string")
    limit = section.get("max_context_bytes", _DEFAULT_LIMIT)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise CodexWorkflowError("[codex].max_context_bytes must be a positive integer")
    return CodexSettings(model=model or None, max_context_bytes=limit)


def effective_model(settings: CodexSettings, override: str | None) -> str:
    """Choose the CLI model when set, otherwise the project model."""
    model = override or settings.model
    if not model or not model.strip():
        raise CodexWorkflowError("set [codex].model or pass --model")
    return model.strip()
