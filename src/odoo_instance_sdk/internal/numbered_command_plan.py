"""Frozen, redacted command plans owned by numbered checkout orchestration."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.internal.alternate_tool import AlternateIdentity, AlternateManifest
    from odoo_instance_sdk.models import DevelopmentEnvironment, EnvironmentDatabaseMode
    from odoo_instance_sdk.project import ProjectConfig
    from odoo_instance_sdk.resources.environment import EnvironmentCheckoutOptions


@dataclass(frozen=True, slots=True)
class _CreateCommandPlan:
    client: OdooClient
    project: ProjectConfig | Path
    home: Path
    number: str
    identity: AlternateIdentity
    checkout: Path
    reference: Path
    checkout_parent: Path
    repository: str
    reference_sha: str
    sdk_sha: str
    environment_base_ref: str | None
    environment_name: str | None
    environment_config_path: Path | None
    environment_db_mode: EnvironmentDatabaseMode
    environment_source_database: str | None
    environment_target_database: str | None
    environment_odoo_bin: Path | None
    environment_python: str | Path | None
    environment_create_venv: bool
    environment_http_port: int | None
    environment_hash_lock: str | Path | None
    environment_hash_lock_sha256: str
    environment_remote_name: str | None
    environment_backup_id: uuid.UUID | str | None

    @classmethod
    def from_options(
        cls,
        *,
        client: OdooClient,
        project: ProjectConfig | Path,
        environment_options: EnvironmentCheckoutOptions,
        home: Path,
        number: str,
        identity: AlternateIdentity,
        checkout: Path,
        reference: Path,
        checkout_parent: Path,
        repository: str,
        reference_sha: str,
        sdk_sha: str,
    ) -> _CreateCommandPlan:
        return cls(
            client,
            project,
            home,
            number,
            identity,
            checkout,
            reference,
            checkout_parent,
            repository,
            reference_sha,
            sdk_sha,
            environment_options.base_ref,
            environment_options.name,
            environment_options.config_path,
            environment_options.db_mode,
            environment_options.source_database,
            environment_options.target_database,
            environment_options.odoo_bin,
            environment_options.python,
            environment_options.create_venv,
            environment_options.http_port,
            environment_options.hash_lock,
            environment_options.hash_lock_sha256 or "",
            environment_options.remote_name,
            environment_options.backup_id,
        )

    def environment_options(self) -> EnvironmentCheckoutOptions:
        from odoo_instance_sdk.resources.environment import EnvironmentCheckoutOptions

        return EnvironmentCheckoutOptions(
            base_ref=self.environment_base_ref,
            name=self.environment_name,
            config_path=self.environment_config_path,
            db_mode=self.environment_db_mode,
            source_database=self.environment_source_database,
            target_database=self.environment_target_database,
            odoo_bin=self.environment_odoo_bin,
            python=self.environment_python,
            create_venv=self.environment_create_venv,
            http_port=self.environment_http_port,
            hash_lock=self.environment_hash_lock,
            hash_lock_sha256=self.environment_hash_lock_sha256,
            remote_name=self.environment_remote_name,
            backup_id=self.environment_backup_id,
        )

    def redacted(self) -> dict[str, str]:
        return {
            "operation": "numbered.create",
            "number": self.number,
            "checkout": str(self.checkout),
            "reference": str(self.reference),
            "checkout_parent": str(self.checkout_parent),
            "repository": self.repository,
            "reference_sha": self.reference_sha,
            "sdk_sha": self.sdk_sha,
        }


@dataclass(frozen=True, slots=True)
class _RemoveCommandPlan:
    client: OdooClient
    home: Path
    number: str
    identity: AlternateIdentity
    checkout_parent: Path | None
    manifest: AlternateManifest

    def redacted(self) -> dict[str, str]:
        return {
            "operation": "numbered.remove",
            "number": self.number,
            "home": str(self.home),
            "checkout": self.manifest.checkout_path or "",
            "checkout_sha": self.manifest.checkout_sha or "",
            "environment_id": self.manifest.environment_id or "",
        }


type CheckoutIdentity = tuple[int, int]


def recovery_payload(
    number: str,
    checkout: Path,
    repository: str,
    checkout_sha: str,
    sdk_sha: str,
    environment: DevelopmentEnvironment,
    interpreter: Path,
    hash_lock_sha256: str,
    checkout_identity: CheckoutIdentity,
    require_pushed: bool,
) -> dict[str, str | int]:
    return {
        "version": 1,
        "kind": "numbered",
        "identifier": number,
        "checkout": str(checkout),
        "repository": repository,
        "checkout_sha": checkout_sha,
        "reference_sha": checkout_sha,
        "sdk_sha": sdk_sha,
        "environment_id": str(environment.id),
        "environment_path": environment.python_environment_path,
        "interpreter_path": str(interpreter),
        "environment_lock": environment.dependency_lock_path,
        "hash_lock_sha256": hash_lock_sha256,
        "quarantine": "",
        "quarantine_identity": "",
        "checkout_identity": f"{checkout_identity[0]}:{checkout_identity[1]}",
        "require_pushed": require_pushed,
    }
