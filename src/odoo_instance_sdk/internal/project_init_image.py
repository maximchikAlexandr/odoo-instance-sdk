"""Captured Compose image-consent phase for project initialization."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from odoo_instance_sdk.exceptions import PostgresImageNotTrustedError

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import JsonValue
    from odoo_instance_sdk.internal.proc import (
        PreparedAction,
        PreparedCommand,
        PreparedStep,
        RunContext,
    )
    from odoo_instance_sdk.resources.postgres import PostgresCluster


@dataclass(frozen=True, slots=True)
class ImageConsentPhase:
    """Immutable image resolution and approval steps for Compose init."""

    resolution: PreparedCommand[str]
    approval: PreparedAction

    @property
    def steps(self) -> tuple[PreparedStep | PreparedAction, ...]:
        return (*self.resolution.steps, self.approval)

    def execute(
        self,
        context: RunContext[dict[str, JsonValue]],
        *,
        cluster: PostgresCluster,
        image: str,
        confirm: Callable[[str, str], None],
    ) -> None:
        digest = self.resolution.callback(cast("RunContext[str]", context))
        try:
            approved = cluster._approved_image_digest()
        except PostgresImageNotTrustedError:
            approved = None
        if approved == digest:
            context.skip(self.approval.step_id)
            return
        confirm(image, digest)
        context.action(self.approval.step_id)
        cluster._approve_image(digest)
        context.complete_action(self.approval.step_id)


def capture_image_consent_phase(cluster: PostgresCluster, image: str) -> ImageConsentPhase:
    """Capture image resolution and approval without invoking either operation."""
    from odoo_instance_sdk.internal.proc import PreparedAction

    return ImageConsentPhase(
        resolution=cluster.resolve_image_digest_command(60.0)._prepared(),
        approval=PreparedAction(
            step_id="init.postgres.approve-image",
            action="approve-postgres-image",
            description="Confirm and persist the selected PostgreSQL image digest",
            details={"image": image},
            mutating=True,
        ),
    )
