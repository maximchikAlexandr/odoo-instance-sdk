"""One adapter around the official Python Codex SDK."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, cast

from odcli_codex.errors import CodexWorkflowError

_INSTRUCTIONS = (
    "Answer from the prompt only. Do not read or write files and do not call tools. "
    "Follow the requested output format exactly."
)


class _ModelId(Protocol):
    id: str
    model: str


class _ModelPage(Protocol):
    data: Sequence[_ModelId] | Sequence[str]
    models: Sequence[_ModelId] | Sequence[str]


def complete(model: str, prompt: str) -> str:
    """Run one read-only Codex turn and return its text."""
    import tempfile

    import openai_codex

    with (
        tempfile.TemporaryDirectory(prefix="odcli-codex-") as directory,
        openai_codex.Codex() as codex,
    ):
        if model not in _ids(cast("_ModelPage", codex.models())):
            raise CodexWorkflowError(
                f"model {model!r} is not available in the installed Codex runtime"
            )
        thread = codex.thread_start(
            model=model,
            sandbox=openai_codex.Sandbox.read_only,
            approval_mode=openai_codex.ApprovalMode.deny_all,
            ephemeral=True,
            cwd=directory,
            developer_instructions=_INSTRUCTIONS,
        )
        result = thread.run(prompt, sandbox=openai_codex.Sandbox.read_only)
        text = getattr(result, "final_response", None)
    if not isinstance(text, str) or not text.strip():
        raise CodexWorkflowError("Codex returned no text")
    return text


def listed_models() -> tuple[str, ...]:
    """Return model ids advertised by the installed Codex runtime."""
    import openai_codex

    with openai_codex.Codex() as codex:
        return _ids(cast("_ModelPage", codex.models()))


def _ids(page: _ModelPage) -> tuple[str, ...]:
    records = getattr(page, "data", None)
    if records is None:
        records = getattr(page, "models", ())
    if not isinstance(records, list | tuple):
        raise CodexWorkflowError("Codex model catalog has no model list")
    names: list[str] = []
    for item in records:
        if isinstance(item, str) and item:
            names.append(item)
            continue
        name = getattr(item, "id", None)
        if not isinstance(name, str) or not name:
            name = getattr(item, "model", None)
        if isinstance(name, str) and name:
            names.append(name)
    if not names:
        raise CodexWorkflowError("Codex model catalog is empty")
    return tuple(names)
