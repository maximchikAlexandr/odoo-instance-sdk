"""Workflow client for translation fill and commit descriptions."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from types import TracebackType

from odcli_codex.codex_runtime import complete as default_complete, listed_models as default_listed
from odcli_codex.config import effective_model, load_settings
from odcli_codex.context_files import context_payload, select_context
from odcli_codex.errors import CodexWorkflowError
from odcli_codex.models import CommitDescription, ContextManifest, FillResult
from odcli_codex.po_catalog import (
    CatalogEntry,
    apply_translations,
    counts,
    empty_entries,
    read_catalog,
    resolve_language,
)
from odoo_instance_sdk.catalog_validation import CatalogValidation, catalog_validation_command
from odoo_instance_sdk.client import OdooClient
from odoo_instance_sdk.execution import ActionStep, Command, JsonValue, action_command
from odoo_instance_sdk.resources.git import GitResource

Complete = Callable[[str, str], str]
ListedModels = Callable[[], tuple[str, ...]]
_DESCRIPTION_LIMIT = 100


class TranslationWorkflow:
    """Fill missing PO entries through one captured command."""

    def __init__(self, complete: Complete, listed_models: ListedModels) -> None:
        self._complete = complete
        self._listed_models = listed_models

    def fill(
        self,
        path: Path,
        *,
        project_root: Path,
        contexts: tuple[str, ...] = (),
        model: str | None = None,
        language: str | None = None,
    ) -> FillResult:
        """Fill one catalog."""
        return self.fill_command(
            path,
            project_root=project_root,
            contexts=contexts,
            model=model,
            language=language,
        ).run()

    def fill_command(
        self,
        path: Path,
        *,
        project_root: Path,
        contexts: tuple[str, ...] = (),
        model: str | None = None,
        language: str | None = None,
    ) -> Command[FillResult]:
        """Capture context, the Codex turn, and the atomic catalog write."""
        catalog_path = path.resolve()
        settings = load_settings(project_root)
        chosen = effective_model(settings, model)
        manifest = select_context(project_root, contexts, limit_bytes=settings.max_context_bytes)
        original, entries = read_catalog(catalog_path)
        resolved_language = resolve_language(catalog_path, entries, language)
        pending = empty_entries(entries)
        holder: dict[str, FillResult] = {}
        prompt = _translation_prompt(
            resolved_language,
            entries,
            pending,
            context_payload(project_root, manifest),
        )
        if len(prompt.encode("utf-8")) > settings.max_context_bytes:
            raise CodexWorkflowError(
                f"translation request exceeds {settings.max_context_bytes} bytes"
            )
        details = _manifest_details(manifest, chosen, resolved_language, str(catalog_path))

        def turn() -> None:
            if chosen not in self._listed_models():
                raise CodexWorkflowError(
                    f"model {chosen!r} is not available in the installed Codex runtime"
                )
            proposals = _parse_translations(self._complete(chosen, prompt))
            updated, rejected, filled = apply_translations(entries, proposals)
            if filled == 0:
                updated = original
            validation = _publish(catalog_path, original, updated)
            summary = counts(read_catalog(catalog_path)[1])
            holder["result"] = FillResult(
                path=str(catalog_path),
                language=resolved_language,
                translated=summary.translated,
                fuzzy=summary.fuzzy,
                untranslated=summary.untranslated,
                filled=filled,
                rejected=rejected,
                validation_status=validation.status if validation is not None else None,
                validation_output=validation.output if validation is not None else None,
                context_paths=tuple(item.path for item in manifest.files),
                context_bytes=manifest.total_bytes,
                context_limit=manifest.limit_bytes,
                model=chosen,
            )

        def idle() -> None:
            summary = counts(entries)
            holder["result"] = FillResult(
                path=str(catalog_path),
                language=resolved_language,
                translated=summary.translated,
                fuzzy=summary.fuzzy,
                untranslated=summary.untranslated,
                filled=0,
                rejected=(),
                validation_status=None,
                validation_output=None,
                context_paths=tuple(item.path for item in manifest.files),
                context_bytes=manifest.total_bytes,
                context_limit=manifest.limit_bytes,
                model=chosen,
            )

        effect = turn if pending else idle
        action = "codex.turn" if pending else "catalog.inspect"

        def result() -> FillResult:
            return holder["result"]

        return action_command(
            (
                ActionStep(
                    step_id="codex.translations",
                    action=action,
                    description="Fill empty PO entries"
                    if pending
                    else "Catalog has no empty entries",
                    details=details,
                    read_only=not pending,
                    mutating=bool(pending),
                ),
            ),
            (effect,),
            result,
        )


class GitMessageWorkflow:
    """Generate one English commit description."""

    def __init__(self, complete: Complete, listed_models: ListedModels) -> None:
        self._complete = complete
        self._listed_models = listed_models

    def message(
        self,
        resource: GitResource,
        *,
        project_root: Path,
        contexts: tuple[str, ...] = (),
        model: str | None = None,
    ) -> str:
        """Return one validated description line."""
        return (
            self.message_command(
                resource,
                project_root=project_root,
                contexts=contexts,
                model=model,
            )
            .run()
            .description
        )

    def message_command(
        self,
        resource: GitResource,
        *,
        project_root: Path,
        contexts: tuple[str, ...] = (),
        model: str | None = None,
    ) -> Command[CommitDescription]:
        """Capture the description request without putting the patch in the plan."""
        settings = load_settings(project_root)
        chosen = effective_model(settings, model)
        manifest = select_context(project_root, contexts, limit_bytes=settings.max_context_bytes)
        patch = resource.staged_patch_command().run()
        prompt = _description_prompt(patch.text, context_payload(project_root, manifest))
        if len(prompt.encode("utf-8")) > settings.max_context_bytes:
            raise CodexWorkflowError(
                f"commit description request exceeds {settings.max_context_bytes} bytes"
            )
        holder: dict[str, CommitDescription] = {}

        def turn() -> None:
            if chosen not in self._listed_models():
                raise CodexWorkflowError(
                    f"model {chosen!r} is not available in the installed Codex runtime"
                )
            description = _one_line(self._complete(chosen, prompt))
            holder["result"] = CommitDescription(
                description=description,
                model=chosen,
                context_paths=tuple(item.path for item in manifest.files),
                context_bytes=manifest.total_bytes,
                context_limit=manifest.limit_bytes,
                patch_bytes=patch.byte_length,
                patch_truncated=patch.truncated,
            )

        def result() -> CommitDescription:
            return holder["result"]

        return action_command(
            (
                ActionStep(
                    step_id="codex.git-message",
                    action="codex.turn",
                    description="Request one English commit description",
                    details={
                        **_manifest_details(manifest, chosen, None, None),
                        "patch_bytes": patch.byte_length,
                        "patch_truncated": patch.truncated,
                    },
                    read_only=True,
                ),
            ),
            (turn,),
            result,
        )


class CodexOdooClient:
    """Public workflows bound to one core client and one Codex completion."""

    def __init__(
        self,
        core: OdooClient,
        *,
        complete: Complete | None = None,
        listed_models: ListedModels | None = None,
    ) -> None:
        self.core = core
        if complete is None or listed_models is None:
            complete = complete or default_complete
            listed_models = listed_models or default_listed
        self.translations = TranslationWorkflow(complete, listed_models)
        self.git = GitMessageWorkflow(complete, listed_models)

    def __enter__(self) -> CodexOdooClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


def _manifest_details(
    manifest: ContextManifest,
    model: str,
    language: str | None,
    path: str | None,
) -> dict[str, JsonValue]:
    paths: list[JsonValue] = [item.path for item in manifest.files]
    return {
        "model": model,
        "language": language,
        "path": path,
        "context_paths": paths,
        "context_bytes": manifest.total_bytes,
        "context_limit": manifest.limit_bytes,
    }


def _translation_prompt(
    language: str,
    entries: tuple[CatalogEntry, ...],
    pending: tuple[CatalogEntry, ...],
    context: str,
) -> str:
    glossary = [
        {"msgid": entry.msgid, "msgstr": entry.fields.get("msgstr", "")}
        for entry in entries
        if entry.msgid and not entry.fuzzy and entry.fields.get("msgstr")
    ]
    missing = [
        {
            "msgid": entry.msgid,
            "msgid_plural": entry.fields.get("msgid_plural", ""),
            "plural": [key for key in entry.fields if key.startswith("msgstr[")],
        }
        for entry in pending
    ]
    return (
        "Fill only the missing Odoo translations. Use existing translations as the glossary. "
        "Use the context only to disambiguate business meaning. Preserve placeholders, "
        'markup, and escapes. Return a JSON object {"translations": {msgid: string or list}} '
        f"for language {language}.\n"
        f"Glossary: {json.dumps(glossary, ensure_ascii=False)}\n"
        f"Missing: {json.dumps(missing, ensure_ascii=False)}\n"
        f"Context:\n{context}"
    )


def _description_prompt(patch: str, context: str) -> str:
    return (
        "Write one English commit description of at most 100 ASCII characters. "
        "Do not include a prefix, module, ticket, or extra lines.\n"
        f"Staged patch:\n{patch}\nContext:\n{context}"
    )


def _parse_translations(raw: str) -> dict[str, str | tuple[str, ...]]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CodexWorkflowError("Codex translation reply is not JSON") from exc
    if not isinstance(payload, dict):
        raise CodexWorkflowError("Codex translation reply must be a JSON object")
    translations = payload.get("translations", payload)
    if not isinstance(translations, dict):
        raise CodexWorkflowError("Codex translation reply has no translations object")
    parsed: dict[str, str | tuple[str, ...]] = {}
    for key, value in translations.items():
        if not isinstance(key, str):
            continue
        if isinstance(value, str):
            parsed[key] = value
        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
            parsed[key] = tuple(value)
    return parsed


def _one_line(raw: str) -> str:
    text = raw.strip()
    if not text or "\n" in text or "\r" in text or len(text) > _DESCRIPTION_LIMIT:
        raise CodexWorkflowError("description must be one non-empty line of at most 100 characters")
    if any(ord(char) < 32 for char in text):
        raise CodexWorkflowError("description must not contain control characters")
    try:
        text.encode("ascii")
    except UnicodeEncodeError as exc:
        raise CodexWorkflowError("description must be English text") from exc
    return text


def _publish(path: Path, original: str, updated: str) -> CatalogValidation | None:
    if updated == original:
        return None
    temporary = path.with_suffix(path.suffix + ".odcli-codex")
    temporary.write_text(updated, encoding="utf-8")
    try:
        read_catalog(temporary)
        validation = catalog_validation_command(temporary).run()
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return validation
