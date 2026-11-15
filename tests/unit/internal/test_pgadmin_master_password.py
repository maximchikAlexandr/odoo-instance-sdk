"""Master-password file, unlock, and selected-database proof."""

from __future__ import annotations

import stat
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import pytest

from odoo_instance_sdk.exceptions import PgAdminUnavailableError
from odoo_instance_sdk.internal import pgadmin_container, pgadmin_files, pgadmin_master_password
from odoo_instance_sdk.internal.pgadmin_files import PgAdminPaths
from odoo_instance_sdk.internal.pgadmin_master_password import unlock_master_password
from odoo_instance_sdk.internal.postgres_compose import ComposeRunner


def _preparation(paths: PgAdminPaths) -> pgadmin_files.PgAdminPreparation:
    return pgadmin_files.prepare_files(
        paths=paths,
        servers_json=(
            '{"Servers":{"1":{"Host":"db","Port":5432,"Username":"odoo",'
            '"MaintenanceDB":"demo","DBRestriction":"demo"}}}'
        ),
        pgpass="db:5432:*:odoo:secret\n",
        fingerprint="f" * 64,
        port=5050,
    )


def test_master_password_file_is_owner_only_and_unmounted(
    local_paths: PgAdminPaths,
) -> None:
    preparation = _preparation(local_paths)
    first = pgadmin_files.store_new_master_password(local_paths)
    path = pgadmin_files.master_password_path(local_paths)
    assert path.is_file()
    assert not path.is_symlink()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert pgadmin_files.load_master_password(local_paths) == first
    assert all(mount.host_path != path for mount in preparation.mounts)
    with pytest.raises(PgAdminUnavailableError):
        pgadmin_files.store_new_master_password(local_paths)


@pytest.mark.parametrize(
    "text",
    [
        "PGADMIN_MASTER_PASSWORD=\n",
        "PGADMIN_MASTER_PASSWORD=value\nOTHER=1\n",
        "NOT_THE_KEY=value\n",
        "PGADMIN_MASTER_PASSWORD=has space\n",
    ],
)
def test_corrupt_master_password_file_fails_closed(local_paths: PgAdminPaths, text: str) -> None:
    local_paths.root.mkdir(parents=True, exist_ok=True)
    path = pgadmin_files.master_password_path(local_paths)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(PgAdminUnavailableError):
        pgadmin_files.load_master_password(local_paths)


def test_insecure_master_password_file_fails_before_prepare(
    local_paths: PgAdminPaths,
) -> None:
    local_paths.root.mkdir(parents=True, exist_ok=True)
    path = pgadmin_files.master_password_path(local_paths)
    path.write_text("PGADMIN_MASTER_PASSWORD=value\n", encoding="utf-8")
    path.chmod(0o644)
    with pytest.raises(PgAdminUnavailableError):
        _preparation(local_paths)


def test_symlink_master_password_file_fails_closed(
    local_paths: PgAdminPaths, tmp_path: Path
) -> None:
    local_paths.root.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "outside"
    target.write_text("PGADMIN_MASTER_PASSWORD=value\n", encoding="utf-8")
    pgadmin_files.master_password_path(local_paths).symlink_to(target)
    with pytest.raises(PgAdminUnavailableError):
        pgadmin_files.load_master_password(local_paths)


def test_unlock_posts_password_only_in_the_body(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[object] = []

    class _Response:
        def __init__(self, body: bytes, url: str) -> None:
            self._body = body
            self._url = url

        def read(self, _size: int) -> bytes:
            return self._body

        def geturl(self) -> str:
            return self._url

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

    class _Opener:
        def open(self, request: object, timeout: float) -> _Response:
            assert timeout > 0
            requests.append(request)
            method = getattr(request, "get_method")()
            url = getattr(request, "full_url")
            if method == "GET":
                return _Response(b'<meta name="csrf-token" content="token">', url)
            return _Response(
                b'{"data":{"present":true,"is_error":false,"errmsg":null}}',
                url,
            )

    monkeypatch.setattr(
        pgadmin_master_password.urllib.request, "build_opener", lambda *_args, **_kwargs: _Opener()
    )
    unlock_master_password(5050, "secret-value", deadline=time.monotonic() + 5)
    post = requests[1]
    assert getattr(post, "full_url") == "http://127.0.0.1:5050/browser/master_password"
    assert b"secret-value" in getattr(post, "data")
    assert "secret-value" not in getattr(post, "full_url")
    assert getattr(post, "headers")["X-pga-csrftoken"] == "token"


def test_rejected_master_password_is_not_success() -> None:
    with pytest.raises(PgAdminUnavailableError):
        pgadmin_master_password._require_present(
            b'{"data":{"present":false,"is_error":true,"errmsg":"Incorrect master password"}}'
        )


class _Runner:
    def __init__(self, probe: str, authenticate: int = 0, probe_code: int = 0) -> None:
        self.probe = probe
        self.authenticate = authenticate
        self.probe_code = probe_code

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        script = " ".join(args)
        if "masterpass_check" in script:
            return subprocess.CompletedProcess(list(args), self.probe_code, self.probe, "")
        return subprocess.CompletedProcess(list(args), self.authenticate, "", "")


def test_existing_profile_without_a_file_is_not_reset(
    local_paths: PgAdminPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        pgadmin_master_password,
        "unlock_master_password",
        lambda *_args, **_kwargs: pytest.fail("reset or unlock"),
    )
    with pytest.raises(PgAdminUnavailableError):
        pgadmin_container._authorize_selected_database(
            _preparation(local_paths),
            "demo",
            runner=cast("ComposeRunner", _Runner("1\n")),
            deadline=1e12,
            planned=False,
        )
    assert not pgadmin_files.master_password_path(local_paths).exists()


def test_denied_database_authentication_fails_the_open(local_paths: PgAdminPaths) -> None:
    with pytest.raises(PgAdminUnavailableError):
        pgadmin_container._authorize_selected_database(
            _preparation(local_paths),
            "demo",
            runner=cast("ComposeRunner", _Runner("0\n", authenticate=1)),
            deadline=1e12,
            planned=False,
        )


def test_unreadable_profile_does_not_create_a_password_file(local_paths: PgAdminPaths) -> None:
    with pytest.raises(PgAdminUnavailableError):
        pgadmin_container._authorize_selected_database(
            _preparation(local_paths),
            "demo",
            runner=cast("ComposeRunner", _Runner("", probe_code=1)),
            deadline=1e12,
            planned=False,
        )
    assert not pgadmin_files.master_password_path(local_paths).exists()


def test_unlock_rejects_a_different_loopback_port(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Response:
        def read(self, _limit: int) -> bytes:
            return b"<html></html>"

        def geturl(self) -> str:
            return "http://127.0.0.1:9/"

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

    monkeypatch.setattr(
        pgadmin_master_password.urllib.request,
        "build_opener",
        lambda *_args, **_kwargs: type(
            "_Opener", (), {"open": lambda self, request, timeout: _Response()}
        )(),
    )
    with pytest.raises(PgAdminUnavailableError):
        unlock_master_password(5050, "secret-value", deadline=time.monotonic() + 5)
