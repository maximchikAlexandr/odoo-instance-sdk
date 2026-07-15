"""Tests for InstanceConfig repr (MT-1)."""

from __future__ import annotations

from odoo_instance_sdk.config import InstanceConfig


def test_repr_redacts_master_password_when_set() -> None:
    cfg = InstanceConfig(base_url="http://localhost:8069", master_password="s3cret")
    r = repr(cfg)
    assert "s3cret" not in r
    assert "master_password=<redacted>" in r


def test_repr_shows_master_password_as_none_when_unset() -> None:
    cfg = InstanceConfig(base_url="http://localhost:8069")
    r = repr(cfg)
    assert "master_password=None" in r


def test_repr_redacts_db_password_when_set() -> None:
    cfg = InstanceConfig(
        base_url="http://localhost:8069",
        db_host="localhost",
        db_port=5432,
        db_user="odoo",
        db_password="dbsecret",
    )
    r = repr(cfg)
    assert "dbsecret" not in r
    assert "db_password=<redacted>" in r


def test_repr_shows_db_password_as_none_when_unset() -> None:
    cfg = InstanceConfig(base_url="http://localhost:8069")
    r = repr(cfg)
    assert "db_password=None" in r
