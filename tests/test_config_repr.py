"""Verify master_pwd never leaks through repr."""

from _helpers import make_client

from odoo_instance_sdk import StartConfig


def test_odoo_client_config_repr_masks_master_pwd() -> None:
    config = make_client("http://localhost:8069").config
    r = repr(config)
    assert "<redacted>" in r, f"master_pwd not redacted in {r!r}"
    # make_client uses master_pwd="admin" — it must not appear
    assert "admin" not in r, f"master_pwd value leaked: {r!r}"
    print("test_odoo_client_config_repr_masks_master_pwd PASSED")


def test_odoo_client_repr_no_master_pwd() -> None:
    client = make_client("http://localhost:8069")
    r = repr(client)
    assert "master_pwd" not in r, f"master_pwd field name leaked: {r!r}"
    assert "admin" not in r, f"master_pwd value leaked: {r!r}"
    print("test_odoo_client_repr_no_master_pwd PASSED")


def test_start_config_repr_masks_db_password() -> None:
    cfg = StartConfig(db_password="s3cret")
    r = repr(cfg)
    assert "s3cret" not in r, f"db_password value leaked: {r!r}"
    assert "<redacted>" in r, f"no redaction marker in {r!r}"
    print("test_start_config_repr_masks_db_password PASSED")


if __name__ == "__main__":
    test_odoo_client_config_repr_masks_master_pwd()
    test_odoo_client_repr_no_master_pwd()
    test_start_config_repr_masks_db_password()
