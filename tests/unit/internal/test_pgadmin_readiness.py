from __future__ import annotations

import time
from urllib.error import URLError

import pytest

from odoo_instance_sdk.exceptions import PgAdminUnavailableError
from odoo_instance_sdk.internal import pgadmin_readiness


class _Response:
    status = 200

    def __init__(self, body: str, final_url: str) -> None:
        self.body = body.encode()
        self.final_url = final_url

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def geturl(self) -> str:
        return self.final_url

    def read(self, _: int) -> bytes:
        return self.body


def test_wait_ready_rejects_login_page_and_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pgadmin_readiness,
        "urlopen",
        lambda *_args, **_kwargs: _Response(
            '<form action="/login"><input type="password"></form>',
            "http://127.0.0.1:5050/",
        ),
    )
    with pytest.raises(PgAdminUnavailableError):
        pgadmin_readiness.wait_ready(5050, deadline=1e12)


def test_wait_ready_retries_transient_refusal_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses: list[object] = [
        URLError("connection refused"),
        _Response("<div id=browser></div>", "http://127.0.0.1:5050/"),
    ]
    calls: list[float] = []
    sleeps: list[float] = []

    def fake_urlopen(*_args: object, **kwargs: object) -> object:
        timeout = kwargs["timeout"]
        assert isinstance(timeout, (int, float))
        calls.append(float(timeout))
        response = responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    monkeypatch.setattr(pgadmin_readiness, "urlopen", fake_urlopen)
    monkeypatch.setattr(time, "sleep", sleeps.append)
    pgadmin_readiness.wait_ready(5050, deadline=1e12)
    assert len(calls) == 2
    assert sleeps == [pgadmin_readiness._READINESS_RETRY_INTERVAL]


def test_wait_ready_repeated_refusal_stops_at_monotonic_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = 0.0
    attempts = 0

    def monotonic() -> float:
        return clock

    def sleep(delay: float) -> None:
        nonlocal clock
        clock += delay

    def refuse(*_args: object, **_kwargs: object) -> object:
        nonlocal attempts
        attempts += 1
        raise URLError("connection refused")

    monkeypatch.setattr(time, "monotonic", monotonic)
    monkeypatch.setattr(time, "sleep", sleep)
    monkeypatch.setattr(pgadmin_readiness, "urlopen", refuse)
    with pytest.raises(PgAdminUnavailableError):
        pgadmin_readiness.wait_ready(5050, deadline=1.0)
    assert attempts == 4
    assert clock == 1.0


def test_wait_ready_does_not_retry_invalid_http_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def invalid(*_args: object, **_kwargs: object) -> _Response:
        nonlocal attempts
        attempts += 1
        response = _Response("<html>starting</html>", "http://127.0.0.1:5050/")
        response.status = 503
        return response

    monkeypatch.setattr(pgadmin_readiness, "urlopen", invalid)
    with pytest.raises(PgAdminUnavailableError):
        pgadmin_readiness.wait_ready(5050, deadline=1e12)
    assert attempts == 1

    monkeypatch.setattr(
        pgadmin_readiness,
        "urlopen",
        lambda *_args, **_kwargs: _Response(
            "<html><body><div id=browser></div></body></html>",
            "http://127.0.0.1:5050/",
        ),
    )
    pgadmin_readiness.wait_ready(5050, deadline=1e12)

    monkeypatch.setattr(
        pgadmin_readiness,
        "urlopen",
        lambda *_args, **_kwargs: _Response("<html>sign in</html>", "http://127.0.0.1:5050/login"),
    )
    with pytest.raises(PgAdminUnavailableError):
        pgadmin_readiness.wait_ready(5050, deadline=1e12)
