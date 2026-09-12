from odoo import fields, models


class OdcliE2eProbe(models.Model):
    _name = "odcli.e2e.probe"
    _description = "OdCLI E2E Probe"

    name = fields.Char(required=True)
    marker = fields.Char(required=True, index=True)
