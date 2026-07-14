"""Verify secrets never leak through repr."""

from odoo_instance_sdk import OdooClientConfig, StartConfig


def test_odoo_client_config_repr_no_secrets() -> None:
    config = OdooClientConfig(executable="/bin/true")
    r = repr(config)
    assert "master" not in r.lower()
    assert "password" not in r.lower()
    assert "secret" not in r.lower()
    print("test_odoo_client_config_repr_no_secrets PASSED")


def test_odoo_client_repr_no_master_pwd() -> None:
    from odoo_instance_sdk import OdooClient

    config = OdooClientConfig(executable="/bin/true")
    client = OdooClient(config=config)
    r = repr(client)
    assert "master_pwd" not in r
    print("test_odoo_client_repr_no_master_pwd PASSED")


def test_start_config_repr_masks_db_password() -> None:
    cfg = StartConfig(db_password="s3cret")
    r = repr(cfg)
    assert "s3cret" not in r, f"db_password value leaked: {r!r}"
    assert "<redacted>" in r, f"no redaction marker in {r!r}"
    print("test_start_config_repr_masks_db_password PASSED")


if __name__ == "__main__":
    test_odoo_client_config_repr_no_secrets()
    test_odoo_client_repr_no_master_pwd()
    test_start_config_repr_masks_db_password()
