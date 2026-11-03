import base64

from odoo.tests import TransactionCase, tagged


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


@tagged("-standard", "odcli_e2e_pass")
class TestOdcliE2eProbePass(TransactionCase):
    def test_result_pass(self):
        self.assertGreater(self.env["ir.model"].search_count([("model", "=", "res.partner")]), 0)


@tagged("-standard", "odcli_e2e_fail")
class TestOdcliE2eProbeFail(TransactionCase):
    def test_result_fail(self):
        self.fail("ODCLI_EXPECTED_FAILURE")


@tagged("-standard", "odcli_e2e_error")
class TestOdcliE2eProbeError(TransactionCase):
    def test_result_error(self):
        self.assertGreater(self.env["ir.model"].search_count([("model", "=", "res.partner")]), 0)
        raise RuntimeError("ODCLI_EXPECTED_ERROR")
