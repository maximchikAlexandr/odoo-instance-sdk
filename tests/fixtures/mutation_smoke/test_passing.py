from odoo_instance_sdk import cli
from odoo_instance_sdk.internal.db_name import validate_db_name


def test_cli_import_and_database_name_validation() -> None:
    assert callable(cli.cli)
    validate_db_name("mutation_smoke")
