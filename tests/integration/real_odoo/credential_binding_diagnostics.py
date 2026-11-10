"""In-memory, secret-free credential binding comparisons for real-Odoo tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from odoo_instance_sdk.resources.instance import OdooInstance

CredentialSource = Literal["cluster_password", "instance_config", "owned_password_file", "missing"]


@dataclass(frozen=True, slots=True, repr=False)
class CredentialBinding:
    """Private snapshot; callers may emit only ``compare_bindings`` output."""

    project_id: str
    endpoint_host: str
    endpoint_port: int
    selected_user: str | None
    selected_password: str | None = field(repr=False)
    source: CredentialSource
    generated_password: str | None = field(repr=False)
    password_file: Path
    password_file_value: str | None = field(repr=False)
    home: Path
    user_root: Path


def capture_binding(
    instance: object, selected: tuple[str, int, str | None, str | None]
) -> CredentialBinding:
    """Capture credential origin and identity privately, without retaining output values."""
    from odoo_instance_sdk.internal import paths
    from odoo_instance_sdk.internal.generated_config import project_generated_config_path
    from odoo_instance_sdk.internal.odoo_config import parse_odoo_config

    config = getattr(instance, "config")
    cluster = getattr(instance, "_postgres_cluster")
    cluster_password = getattr(cluster, "_password", None)
    configured_password = getattr(config, "db_password", None)
    password_file = getattr(cluster, "password_file")
    password_file_value = (
        password_file.read_text(encoding="utf-8").strip() or None
        if password_file.is_file()
        else None
    )
    if cluster_password:
        source: CredentialSource = "cluster_password"
    elif configured_password:
        source = "instance_config"
    elif getattr(cluster, "owned", False) and password_file_value:
        source = "owned_password_file"
    else:
        source = "missing"

    generated_path = project_generated_config_path(getattr(cluster, "_repository_root"))
    generated_password = (
        parse_odoo_config(generated_path).get("db_password") if generated_path.is_file() else None
    )
    return CredentialBinding(
        project_id=str(getattr(cluster, "_project_id")),
        endpoint_host=str(selected[0]),
        endpoint_port=int(selected[1]),
        selected_user=selected[2],
        selected_password=selected[3],
        source=source,
        generated_password=generated_password,
        password_file=password_file,
        password_file_value=password_file_value,
        home=Path.home().resolve(),
        user_root=paths.get_user_root(ensure_exists=False).resolve(),
    )


def compare_bindings(before: CredentialBinding, after: CredentialBinding) -> dict[str, object]:
    """Return only fixed predicates and approved source labels."""
    return {
        "cluster_identity_unchanged": before.project_id == after.project_id,
        "endpoint_identity_unchanged": (
            before.endpoint_host == after.endpoint_host
            and before.endpoint_port == after.endpoint_port
        ),
        "user_identity_unchanged": before.selected_user == after.selected_user,
        "credential_source_before": before.source,
        "credential_source_after": after.source,
        "password_present_before": bool(before.selected_password),
        "password_present_after": bool(after.selected_password),
        "selected_password_matches_baseline": (before.selected_password == after.selected_password),
        "generated_password_matches_selected_before": (
            before.generated_password == before.selected_password
        ),
        "generated_password_matches_selected_after": (
            after.generated_password == after.selected_password
        ),
        "generated_password_matches_owned_file_before": (
            before.generated_password == before.password_file_value
        ),
        "generated_password_matches_owned_file_after": (
            after.generated_password == after.password_file_value
        ),
        "owned_password_file_unchanged": (before.password_file_value == after.password_file_value),
        "password_file_binding_unchanged": before.password_file == after.password_file,
        "home_binding_unchanged": before.home == after.home,
        "user_root_binding_unchanged": before.user_root == after.user_root,
        "user_root_matches_home_before": before.user_root == before.home / ".odcli",
        "user_root_matches_home_after": after.user_root == after.home / ".odcli",
    }


def capture_project_binding(project_root: Path) -> CredentialBinding:
    """Capture the generated-config credential path at the publication boundary."""
    from odoo_instance_sdk.internal.generated_config import project_generated_config_path
    from odoo_instance_sdk.internal.pg.inventory import _credentials
    from odoo_instance_sdk.models import StartConfig
    from odoo_instance_sdk.resources.postgres import PostgresCluster

    cluster = PostgresCluster.from_project(project_root)
    config = StartConfig.from_odoo_config(project_generated_config_path(project_root))
    instance = SimpleNamespace(config=config, _postgres_cluster=cluster)
    return capture_binding(instance, _credentials(cast("OdooInstance", instance)))
