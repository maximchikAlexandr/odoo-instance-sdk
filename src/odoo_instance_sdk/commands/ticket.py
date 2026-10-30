from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import click

else:
    import rich_click as click

from odoo_instance_sdk.exceptions import PlanValidationError
from odoo_instance_sdk.internal.ticket_branch_allocation import validate_ticket


class TicketType(click.ParamType[str]):
    name = "TICKET"

    def convert(self, value: str, param: click.Parameter | None, ctx: click.Context | None) -> str:
        try:
            return validate_ticket(str(value))
        except PlanValidationError:
            self.fail("expected ticket like PROJ-123", param, ctx)


TICKET = TicketType()
