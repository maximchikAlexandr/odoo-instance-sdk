from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import httpx

from odoo_instance_sdk._local_guard import assert_local
from odoo_instance_sdk._platform_cache import default_backup_dir
from odoo_instance_sdk.exceptions import DatabaseError
from odoo_instance_sdk.models import (
    BackupArtifact,
    DropResult,
    RestoreResult,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient


@dataclass
class DatabaseResource:
    """Database operations resource."""

    _client: OdooClient

    def _url(self, path: str) -> str:
        return f"{self._client.config.base_url.rstrip('/')}/web/database/{path}"

    @contextlib.contextmanager
    def _http(self, timeout: float | None = None) -> Iterator[httpx.Client]:
        cfg = self._client.config
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
        config = self._client.config

        dest_dir = Path(dest) if dest is not None else (
            Path(config.backup_dir) if config.backup_dir is not None else default_backup_dir()
        )
        dest_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{db}.{format}"
        filepath = (dest_dir / filename).resolve()

        data = {
            "master_pwd": config.master_pwd,
            "name": db,
            "backup_format": format,
            "filestore": "1" if include_filestore else "0",
        }

        url = self._url("backup")

        with self._http(timeout=timeout) as http, http.stream("POST", url, data=data) as response:
            if response.status_code != 200:
                body = response.read()
                raise DatabaseError(
                    status_code=response.status_code,
                    message=f"Backup failed: HTTP {response.status_code}",
                    body=body,
                )

            with open(filepath, "wb") as f:
                for chunk in response.iter_bytes():
                    f.write(chunk)

        return BackupArtifact(
            path=filepath,
            source_db=db,
            format=format,
            has_filestore=include_filestore,
            source_base_url=config.base_url,
        )

    def list(
        self,
        *,
        timeout: float | None = None,
    ) -> list[str]:
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
            if response.status_code != 200:
                raise DatabaseError(
                    status_code=response.status_code,
                    message=f"Failed to list databases: HTTP {response.status_code}",
                    body=response.content,
                )

            data = response.json()
            databases: list[str] = data.get("result", [])
            return databases

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

            if response.status_code == 302:
                return DropResult(db=db)
            raise DatabaseError(
                status_code=response.status_code,
                message=f"Failed to drop database '{db}'",
                body=response.content,
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

        if response.status_code != 200:
            body = response.content
            try:
                msg = response.json().get("error", response.text)
            except (json.JSONDecodeError, ValueError):
                msg = response.text
            raise DatabaseError(
                status_code=response.status_code,
                message=msg,
                body=body,
            )

        return RestoreResult(new_db=new_db, source=artifact)
