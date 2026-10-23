"""Owner-only administrator configuration and login authentication."""

from __future__ import annotations

import os
import re
import secrets
import stat
import threading
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.internal.paths import get_admin_config_path

from .admin_support import (
    AdminUser,
    LoginFailed,
    _AdminAuthBase,
    _AdminUser,
    _dashboard_symbol,
    _PasswordHashFactory,
)

if TYPE_CHECKING:
    from .admin_support import Request

_SESSION_KEY_MINIMUM = 32
_ADMIN_USER = "admin"
_PRIVATE_BITS = stat.S_IRWXG | stat.S_IRWXO
_ARGON2_HASH = re.compile(r"\$argon2(?:id|i|d)\$[A-Za-z0-9=,.$+/:-]+\Z")


class _LoginAttemptBudget:
    """One process-wide budget for the single administrator account."""

    _WINDOW_SECONDS = 60.0
    _MAX_FAILURES = 5
    _MAX_BACKOFF_SECONDS = 30.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._window_started = time.monotonic()
        self._failures = 0
        self._in_flight = 0
        self._blocked_until = 0.0

    def allow(self) -> bool:
        now = time.monotonic()
        with self._lock:
            if now - self._window_started >= self._WINDOW_SECONDS:
                self._window_started = now
                self._failures = 0
                self._blocked_until = 0.0
            if now < self._blocked_until or self._failures + self._in_flight >= self._MAX_FAILURES:
                return False
            self._in_flight += 1
            return True

    def failed(self) -> None:
        now = time.monotonic()
        with self._lock:
            self._in_flight = max(0, self._in_flight - 1)
            if now - self._window_started >= self._WINDOW_SECONDS:
                self._window_started = now
                self._failures = 0
            self._failures += 1
            if self._failures >= self._MAX_FAILURES:
                exponent = self._failures - self._MAX_FAILURES
                delay = min(2.0**exponent, self._MAX_BACKOFF_SECONDS)
                self._blocked_until = max(self._blocked_until, now + delay)

    def succeeded(self) -> None:
        now = time.monotonic()
        with self._lock:
            self._in_flight = max(0, self._in_flight - 1)
            self._window_started = now
            self._failures = 0
            self._blocked_until = 0.0


_LOGIN_ATTEMPTS = _LoginAttemptBudget()


@dataclass(frozen=True, slots=True, repr=False)
class AdminSettings:
    password_hash: str
    session_key: str

    def __repr__(self) -> str:
        return "AdminSettings(password_hash=<redacted>, session_key=<redacted>)"

    @classmethod
    def load(cls, path: Path | None = None) -> AdminSettings:
        selected = get_admin_config_path(ensure_exists=False)
        if path is not None and path.resolve(strict=False) != selected.resolve(strict=False):
            raise ConfigError("administrator configuration must use the canonical user config path")
        try:
            descriptor = os.open(
                selected,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            )
        except OSError as exc:
            raise ConfigError("administrator configuration is unavailable") from exc
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) & _PRIVATE_BITS
            ):
                raise ConfigError("administrator configuration must be a private regular file")
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                try:
                    raw = tomllib.load(handle)
                except (OSError, tomllib.TOMLDecodeError) as exc:
                    raise ConfigError("administrator configuration is invalid") from exc
        finally:
            if descriptor != -1:
                os.close(descriptor)
        data = raw.get("admin", raw)
        if not isinstance(data, dict) or set(data) != {"password_hash", "session_key"}:
            raise ConfigError(
                "administrator configuration must contain password_hash and session_key"
            )
        password_hash = data.get("password_hash")
        session_key = data.get("session_key")
        if not isinstance(password_hash, str) or not _ARGON2_HASH.fullmatch(password_hash):
            raise ConfigError("administrator password hash is unsupported")
        if (
            not isinstance(session_key, str)
            or len(session_key) < _SESSION_KEY_MINIMUM
            or not session_key.strip()
            or "\x00" in session_key
        ):
            raise ConfigError("administrator session key is invalid")
        return cls(password_hash=password_hash, session_key=session_key)


class _AdminAuth(_AdminAuthBase):
    def __init__(self, settings: AdminSettings) -> None:
        super().__init__()
        self._settings = settings
        self._dummy_hash: str | None = None

    async def login(
        self, username: str, password: str, remember_me: bool, request: Request
    ) -> None:
        del remember_me
        if not _LOGIN_ATTEMPTS.allow():
            raise LoginFailed("Invalid username or password")
        PasswordHash = cast("_PasswordHashFactory", _dashboard_symbol("pwdlib", "PasswordHash"))
        verifier = PasswordHash.recommended()
        valid_user = username == _ADMIN_USER
        try:
            if self._dummy_hash is None:
                self._dummy_hash = verifier.hash(secrets.token_urlsafe(32))
            valid = verifier.verify(
                password, self._settings.password_hash if valid_user else self._dummy_hash
            )
        except Exception as exc:
            _LOGIN_ATTEMPTS.failed()
            raise LoginFailed("Invalid username or password") from exc
        if not valid_user or not valid:
            _LOGIN_ATTEMPTS.failed()
            raise LoginFailed("Invalid username or password")
        _LOGIN_ATTEMPTS.succeeded()
        request.session["admin_user"] = _ADMIN_USER

    async def logout(self, request: Request) -> None:
        request.session.clear()

    async def authenticate(self, request: Request) -> _AdminUser | None:
        if request.session.get("admin_user") == _ADMIN_USER:
            return AdminUser(username=_ADMIN_USER)
        return None
