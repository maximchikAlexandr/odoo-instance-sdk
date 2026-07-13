from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import httpx

from odoo_instance_sdk._local_guard import assert_local, warn_if_cleartext_auth
from odoo_instance_sdk._platform_cache import default_backup_dir
from odoo_instance_sdk.exceptions import DatabaseError
from odoo_instance_sdk.models import (
    BackupArtifact,
    DropResult,
    RestoreResult,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient

_REDACTED = b"<redacted>"


def _redact(data: bytes, secret: str) -> bytes:
    """Redact secret from bytes data. Skips if secret is too short (<4 chars) to avoid false-positive oracle."""
    if not secret or len(secret) < 4:
        return data
    sb = secret.encode()
    if sb in data:
        return data.replace(sb, _REDACTED)
    return data


@dataclass
class DatabaseResource:
    """Database operations resource."""

    _client: OdooClient

    def _url(self, path: str) -> str:
        return f"{self._client.config.base_url.rstrip('/')}/web/database/{path}"

    @contextlib.contextmanager
    def _http(self, timeout: float | None = None) -> Iterator[httpx.Client]:
        cfg = self._client.config
        warn_if_cleartext_auth(cfg.base_url, stacklevel=3)
        with httpx.Client(
            auth=("admin", cfg.master_pwd),
            timeout=httpx.Timeout(timeout or cfg.http_timeout),
        ) as http:
            yield http

    def backup(
        self,
        db: str,
        *,
        format: Literal["zip", "dump"] = "zip",
        include_filestore: bool = True,
        dest: str | Path | None = None,
        timeout: float | None = None,
    ) -> BackupArtifact:
        if format not in ("zip", "dump"):
            raise ValueError(f"Invalid format: {format!r} — expected 'zip' or 'dump'")

        # ponytail: basename check stops path traversal via crafted db names
        _safe_db = Path(db).name
        if _safe_db != db:
            raise ValueError(f"Invalid database name: {db!r} — path separators not allowed")

        config = self._client.config

        dest_dir = (
            Path(dest)
            if dest is not None
            else (
                Path(config.backup_dir) if config.backup_dir is not None else default_backup_dir()
            )
        )
        dest_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{_safe_db}.{format}"
        filepath = (dest_dir / filename).resolve()

        data = {
            "master_pwd": config.master_pwd,
            "name": _safe_db,
            "backup_format": format,
            "filestore": "1" if include_filestore else "0",
        }

        url = self._url("backup")

        with self._http(timeout=timeout) as http, http.stream("POST", url, data=data) as response:
            if response.status_code != HTTPStatus.OK:
                body = response.read()
                raise DatabaseError(
                    status_code=response.status_code,
                    message=f"Backup failed: {HTTPStatus(response.status_code).phrase}",
                    body=_redact(body, config.master_pwd),
                )

            with open(filepath, "wb") as f:
                for chunk in response.iter_bytes():
                    f.write(chunk)

        return BackupArtifact(
            path=filepath,
            source_db=_safe_db,
            format=format,
            has_filestore=include_filestore,
            source_base_url=config.base_url,
        )

    def list(
        self,
        *,
        timeout: float | None = None,
    ) -> list[str]:
        config = self._client.config
        with self._http(timeout=timeout) as http:
            response = http.post(
                self._url("list"),
                json={
                    "jsonrpc": "2.0",
                    "method": "call",
                    "params": {},
                    "id": 1,
                },
            )
            if response.status_code != HTTPStatus.OK:
                raise DatabaseError(
                    status_code=response.status_code,
                    message=f"Failed to list databases: {HTTPStatus(response.status_code).phrase}",
                    body=_redact(response.content, config.master_pwd),
                )

            try:
                data = response.json()
            except json.JSONDecodeError:
                raise DatabaseError(
                    status_code=response.status_code,
                    message="Failed to parse list response as JSON",
                    body=_redact(response.content, config.master_pwd),
                )
            result = data.get("result", [])
            if not isinstance(result, list):
                raise DatabaseError(
                    status_code=response.status_code,
                    message="Unexpected list response format: expected list",
                    body=_redact(response.content, config.master_pwd),
                )
            return result

    def exists(
        self,
        db: str,
        *,
        timeout: float | None = None,
    ) -> bool:
        return db in self.list(timeout=timeout)

    def drop(
        self,
        db: str,
        *,
        timeout: float | None = None,
    ) -> DropResult:
        config = self._client.config
        assert_local(config.base_url)

        with self._http(timeout=timeout) as http:
            data: dict[str, str] = {"master_pwd": config.master_pwd, "name": db}
            response = http.post(self._url("drop"), data=data)

            if response.status_code == HTTPStatus.FOUND:
                return DropResult(db=db)
            raise DatabaseError(
                status_code=response.status_code,
                message=f"Failed to drop database '{db}': {HTTPStatus(response.status_code).phrase}",
                body=_redact(response.content, config.master_pwd),
            )

    def restore(
        self,
        artifact: BackupArtifact,
        new_db: str,
        *,
        timeout: float | None = None,
    ) -> RestoreResult:
        config = self._client.config

        assert_local(config.base_url)

        backup_path = artifact.path
        if not backup_path.exists():
            raise FileNotFoundError(backup_path)

        with self._http(timeout=timeout) as http, open(backup_path, "rb") as f:
            files = {
                "backup_file": (backup_path.name, f, "application/octet-stream"),
            }
            fields = {
                "master_pwd": config.master_pwd,
                "name": new_db,
                "copy": "True",
            }
            response = http.post(self._url("restore"), data=fields, files=files)

        if response.status_code != HTTPStatus.OK:
            body = response.content
            try:
                server_msg = response.json().get("error", response.text)
            except (json.JSONDecodeError, ValueError):
                server_msg = response.text
            raise DatabaseError(
                status_code=response.status_code,
                message=_redact(
                    f"Restore failed: {HTTPStatus(response.status_code).phrase}: {server_msg}".encode(),
                    config.master_pwd,
                ).decode("utf-8", errors="replace"),
                body=_redact(body, config.master_pwd),
            )

        return RestoreResult(new_db=new_db, source=artifact)
