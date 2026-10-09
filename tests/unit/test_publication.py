from __future__ import annotations

from pathlib import Path

import pytest

from odoo_instance_sdk.client import OdooClient
from odoo_instance_sdk.config import OdooClientConfig
from odoo_instance_sdk.exceptions import ConfigError, PublicationError
from odoo_instance_sdk.internal.paths import get_user_root
from odoo_instance_sdk.internal.proc import PreparedStep, ProcessResult, RecordingExecutor
from odoo_instance_sdk.resources.publication import (
    PublicationSettings,
    PublicationTarget,
    render_panel_route,
)


def _settings() -> PublicationSettings:
    root = get_user_root()
    return PublicationSettings(
        domain_suffix="example.test",
        caddy_executable="caddy",
        caddy_control_endpoint="http://127.0.0.1:2019",
        owned_route_file=root / "routes.caddy",
        basic_auth_username="odoo",
        basic_auth_password_hash="$2a$10$abcdefghijklmnopqrstuu",
        panel_host_label="panel",
        trusted_proxy_addresses=("127.0.0.1",),
    )


def _client() -> OdooClient:
    return OdooClient(config=OdooClientConfig(executable="odoo"))


def _process(step: PreparedStep, *, returncode: int = 0) -> ProcessResult:
    return ProcessResult(
        argv=step.argv,
        returncode=returncode,
        stdout="",
        stderr="",
        duration=0.0,
        cwd=None,
        environment=(),
    )


def test_settings_reject_plaintext_and_non_loopback_control(tmp_path: Path) -> None:
    root = tmp_path / ".odcli"
    with pytest.raises(ConfigError):
        PublicationSettings(
            domain_suffix="example.test",
            caddy_executable="caddy",
            caddy_control_endpoint="http://127.0.0.1:2019",
            owned_route_file=root / "routes.caddy",
            basic_auth_username="odoo",
            basic_auth_password_hash="plaintext",
            trusted_proxy_addresses=("127.0.0.1",),
        )
    with pytest.raises(ConfigError):
        PublicationSettings(
            domain_suffix="*.example.test",
            caddy_executable="caddy",
            caddy_control_endpoint="http://0.0.0.0:2019",
            owned_route_file=root / "routes.caddy",
            basic_auth_username="odoo",
            basic_auth_password_hash="$2a$10$abcdefghijklmnopqrstuu",
            trusted_proxy_addresses=("127.0.0.1",),
        )


def test_publish_is_stable_and_unpublish_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    settings = _settings()
    target = PublicationTarget(
        owner_kind="project",
        owner_id="project_abc_123",
        project_id="project_abc_123",
        local_endpoint="http://127.0.0.1:8069",
    )
    executor = RecordingExecutor()
    client = _client()

    first = client.publication.publish_command(target, settings=settings, executor=executor).run()
    second = client.publication.publish_command(target, settings=settings, executor=executor).run()
    route_text = settings.owned_route_file.read_text()
    removed = client.publication.unpublish_command(
        ("project", target.owner_id), settings=settings, executor=executor
    ).run()
    absent = client.publication.unpublish_command(
        ("project", target.owner_id), settings=settings, executor=executor
    ).run()

    assert first.external_url == "https://project-abc-123.example.test"
    assert "basic_auth" in route_text
    assert "/websocket" in route_text
    assert "project-abc-123.example.test" in route_text
    assert "panel.example.test" in render_panel_route("http://127.0.0.1:9000", settings)
    assert second.status == "already_published"
    assert removed.status == "unpublished"
    assert absent.status == "already_absent"


def test_reload_failure_restores_prior_route_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    settings = _settings()
    client = _client()
    first = PublicationTarget(
        owner_kind="project",
        owner_id="project_a",
        project_id="project_a",
        local_endpoint="http://127.0.0.1:8069",
    )
    second = PublicationTarget(
        owner_kind="environment",
        owner_id="env-b",
        project_id="project_a",
        environment_id="env-b",
        local_endpoint="http://127.0.0.1:8070",
    )
    client.publication.publish_command(first, settings=settings, executor=RecordingExecutor()).run()
    previous = settings.owned_route_file.read_bytes()

    def fail_reload(step: PreparedStep) -> ProcessResult:
        return _process(
            step,
            returncode=1 if step.step_id == "publication.publish.caddy.reload" else 0,
        )

    with pytest.raises(PublicationError, match="rejected"):
        client.publication.publish_command(
            second,
            settings=settings,
            executor=RecordingExecutor(result_factory=fail_reload),
        ).run()

    assert settings.owned_route_file.read_bytes() == previous
