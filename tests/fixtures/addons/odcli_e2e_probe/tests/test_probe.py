import base64

from odoo.tests import TransactionCase


class TestOdcliE2eProbe(TransactionCase):
    def test_deterministic_record_and_filestore_attachment(self):
        record = self.env["odcli.e2e.probe"].search(
            [("marker", "=", "ODCLI-E2E-RESTORED")], limit=1
        )
        self.assertEqual(record.name, "Pinned Odoo 19 fixture")
        attachment = self.env["ir.attachment"].search(
            [("name", "=", "odcli-e2e-attachment.txt")], limit=1
        )
        self.assertTrue(attachment.store_fname)
        self.assertEqual(base64.b64decode(attachment.datas), b"OdCLI filestore probe\n")
