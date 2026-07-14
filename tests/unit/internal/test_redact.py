from __future__ import annotations

from odoo_instance_sdk.internal.redact import format_error


class _Exc(Exception):
    pass


def test_redact_unquoted() -> None:
    assert format_error(_Exc("master_pwd=secret")) == "master_pwd=***"


def test_redact_double_quoted_with_spaces() -> None:
    assert format_error(_Exc('password="my pass"')) == 'password="***"'


def test_redact_single_quoted_with_spaces() -> None:
    assert format_error(_Exc("admin_passwd='a b c'")) == "admin_passwd='***'"


def test_non_secret_preserved() -> None:
    assert format_error(_Exc("name=mydb")) == "name=mydb"
