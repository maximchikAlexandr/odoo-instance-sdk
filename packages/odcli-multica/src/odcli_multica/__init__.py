"""Public typed Multica/Odoo composition primitives."""

from odcli_multica.client import (
    MulticaOdooClient,
    PrepareCommand,
    read_gitlab_credential_mappings,
)
from odcli_multica.models import (
    ContextRequest,
    GitLabCredentialContext,
    GitLabCredentialIdentity,
    GitLabCredentialMapping,
    MergeRequestPublicationResult,
    MulticaCompatibility,
    PreparationRequest,
    RootCreatorContext,
    TaskContext,
    VerifiedTaskContext,
)

__all__ = [
    "ContextRequest",
    "GitLabCredentialContext",
    "GitLabCredentialIdentity",
    "GitLabCredentialMapping",
    "MergeRequestPublicationResult",
    "MulticaCompatibility",
    "MulticaOdooClient",
    "PreparationRequest",
    "PrepareCommand",
    "RootCreatorContext",
    "TaskContext",
    "VerifiedTaskContext",
    "read_gitlab_credential_mappings",
]
