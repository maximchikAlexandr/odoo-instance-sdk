"""Functions core ``odcli`` calls after an explicit Codex flag."""

from __future__ import annotations

from pathlib import Path

from odcli_codex.client import CodexOdooClient
from odcli_codex.models import FillResult
from odoo_instance_sdk import OdooClient, OdooClientConfig
from odoo_instance_sdk.resources.git import GitResource


def fill_exported_catalogs(
    paths: tuple[Path, ...],
    *,
    contexts: tuple[str, ...],
    model: str | None,
    project_root: Path,
    language: str | None = None,
) -> tuple[FillResult, ...]:
    """Fill catalogs that core export has already written."""
    client = _client()
    return tuple(
        client.translations.fill(
            path,
            project_root=project_root,
            contexts=contexts,
            model=model,
            language=language,
        )
        for path in paths
    )


def generate_commit_description(
    resource: GitResource,
    *,
    contexts: tuple[str, ...],
    model: str | None,
    project_root: Path,
) -> str:
    """Return one description and leave commit planning to core."""
    return _client().git.message(
        resource,
        project_root=project_root,
        contexts=contexts,
        model=model,
    )


def _client() -> CodexOdooClient:
    return CodexOdooClient(OdooClient(config=OdooClientConfig(executable="odoo")))
