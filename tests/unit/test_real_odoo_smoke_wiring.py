"""smoke tests for the real-Odoo CI contract."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Literal, cast

import pytest

from scripts import real_odoo_bootstrap as bootstrap
from scripts import real_odoo_evidence as evidence
from tests.integration.real_odoo import test_smoke as smoke
from tests.integration.real_odoo.conftest import E2ERuntime
from tests.unit.real_odoo_ci_support import _evidence_contract


@pytest.mark.parametrize("cache_class", ["cold", "warm"])
def test_evidence_exercises_smoke_budget_classification(
    tmp_path: Path, cache_class: Literal["cold", "warm"]
) -> None:
    timing_path = tmp_path / "timing.json"
    timing_path.write_text(
        json.dumps(
            {
                "phases": {
                    "setup": {"duration_seconds": 1},
                    "test": {"duration_seconds": 2},
                    "cleanup": {"duration_seconds": 1},
                }
            }
        )
    )
    report = evidence._budget_report(
        timing_path,
        status="success",
        tier="smoke",
        cache_class=cache_class,
        artifact_bytes=10,
    )
    assert report["cache_class"] == cache_class
    assert report["ok"] is True


@pytest.mark.parametrize("cache_class", ["cold", "warm"])
def test_smoke_bootstrap_and_evidence_emit_cache_class_and_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cache_class: Literal["cold", "warm"],
) -> None:
    """Exercise the smoke cache matrix through bootstrap and packaging."""
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    monkeypatch.setattr(bootstrap, "_cache_os_arch", lambda: ("Linux", "X64"))
    monkeypatch.setattr(bootstrap, "prerequisite_checks", lambda _tier, _platform: {})
    (tmp_path / "uv.lock").write_bytes(b"repository lock")
    requirements = tmp_path / ".cache" / "odoo-requirements.txt"
    requirements.parent.mkdir()
    requirements.write_bytes(b"pinned requirements")
    source = tmp_path / "evidence"
    source.mkdir()
    _evidence_contract(source)
    monkeypatch.setenv("ODCLI_E2E_SOURCE_CACHE_HIT", "false")
    monkeypatch.setenv("ODCLI_E2E_UV_CACHE_HIT", str(cache_class == "warm").lower())
    bootstrap_manifest = bootstrap.bootstrap(
        "smoke", source / "bootstrap.json", platform_name="linux/amd64", artifact_root=source
    )
    emitted_cache = bootstrap_manifest["cache"]
    assert isinstance(emitted_cache, dict)
    emitted_class = cast("Literal['cold', 'warm']", emitted_cache["class"])
    assert emitted_class == cache_class
    canary = tmp_path / "canary"
    canary.write_text("smoke-canary\n", encoding="utf-8")
    packaged = evidence.package_evidence(
        source,
        tmp_path / "smoke.tar.gz",
        status="success",
        canary_file=canary,
        tier="smoke",
        cache_class=emitted_class,
    )
    packaged_budget = packaged["budget"]
    assert isinstance(packaged_budget, dict)
    assert packaged_budget["cache_class"] == cache_class
    assert packaged_budget["ok"] is True


def test_smoke_bootstrap_ignores_label_only_cache_class(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bootstrap, "prerequisite_checks", lambda _tier, _platform: {})
    monkeypatch.setenv("ODCLI_E2E_CACHE_CLASS", "warm")
    monkeypatch.setenv("ODCLI_E2E_SOURCE_CACHE_HIT", "false")
    monkeypatch.setenv("ODCLI_E2E_UV_CACHE_HIT", "false")
    manifest = bootstrap.bootstrap(
        "smoke", tmp_path / "bootstrap.json", platform_name="linux/amd64"
    )
    cache = manifest["cache"]
    assert isinstance(cache, dict)
    assert cache["class"] == "cold"


def test_smoke_target_wiring_keeps_source_and_target_endpoints_distinct(tmp_path: Path) -> None:
    source_password = tmp_path / "source-password"
    target_password = tmp_path / "target-password"
    source_password.write_text("source-master\n", encoding="utf-8")
    target_password.write_text("target-master\n", encoding="utf-8")
    target_secret = tmp_path / "target-pg-password"
    target_secret.write_text("pg-secret\n", encoding="utf-8")
    target = SimpleNamespace(
        master_password_file=target_password,
        secret_file=target_secret,
        container_config_file=tmp_path / "container.conf",
        config_file=tmp_path / "host.conf",
        root=tmp_path / "target-root",
        topology=SimpleNamespace(target_postgres_port=15432),
        reservations=(None, None, None, SimpleNamespace(port=18069)),
    )
    source = SimpleNamespace(master_password_file=source_password)
    smoke._align_target_master_password(cast("E2ERuntime", target), cast("E2ERuntime", source))
    host_config = (tmp_path / "host.conf").read_text(encoding="utf-8")
    container_config = (tmp_path / "container.conf").read_text(encoding="utf-8")
    assert "admin_passwd = source-master" in host_config
    assert "db_host = 127.0.0.1" in host_config
    assert "db_port = 15432" in host_config
    assert "http_port = 18069" in host_config
    assert "admin_passwd = source-master" in container_config
    assert "db_host = target_postgres" in container_config
    assert target.root.joinpath("target-data").as_posix() in host_config
    assert "source_postgres" not in host_config


def test_smoke_auxiliary_proxy_is_project_bound(tmp_path: Path) -> None:
    proxy = smoke._write_target_proxy(tmp_path, "http://127.0.0.1:18069", 18070)
    assert proxy.stat().st_mode & 0o777 == 0o700
    proxy_source = proxy.read_text(encoding="utf-8")
    assert "http://127.0.0.1:18069" in proxy_source
    assert '("127.0.0.1", 18070)' in proxy_source


def test_smoke_releases_target_port_before_compose_and_waits_for_health(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []

    class Reservation:
        port = 18069

        def release(self) -> None:
            events.append("release")

    class Lifecycle:
        def __init__(self, _compose_file: Path, _project_name: str) -> None:
            pass

        def run(self, *_args: str) -> subprocess.CompletedProcess[str]:
            events.append("compose")
            return subprocess.CompletedProcess([], 0, "", "")

    compose_file = tmp_path / "compose.yaml"
    compose_file.write_text(
        '  target_init:\n    command: ["odoo", '
        '"--database=odcli_e2e_sentinel_run", "--init=base", "--stop-after-init"]\n',
        encoding="utf-8",
    )
    runtime = SimpleNamespace(
        compose_file=compose_file,
        topology=SimpleNamespace(
            project_name="odcli-e2e-project-run",
            target_sentinel_database="odcli_e2e_sentinel_run",
        ),
        reservations=(None, None, None, Reservation()),
    )
    monkeypatch.setattr(smoke, "ComposeLifecycle", Lifecycle)
    monkeypatch.setattr(smoke, "wait_for_http", lambda *_args, **_kwargs: events.append("health"))
    smoke._start_target_odoo(cast("E2ERuntime", runtime))
    assert events == ["release", "compose", "health", "health"]
