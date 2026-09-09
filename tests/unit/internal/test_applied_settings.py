from __future__ import annotations

import json
from pathlib import Path

import pytest

from odoo_instance_sdk.internal.applied_settings import (
    LEGACY_UNKNOWN_APPLIED_SETTINGS_JSON,
    AppliedSettingsCodec,
    AppliedSettingsError,
)


def test_legacy_document_is_versioned_and_all_components_unknown() -> None:
    codec = AppliedSettingsCodec()
    decoded = codec.decode(LEGACY_UNKNOWN_APPLIED_SETTINGS_JSON)
    assert decoded["version"] == 1
    components = decoded["components"]
    assert isinstance(components, dict)
    assert set(components) == {"python", "dependencies", "odoo", "addons", "git"}
    assert all(component == {"status": "unknown"} for component in components.values())


def test_codec_normalizes_components_and_fingerprints_secret_free_dependencies(
    tmp_path: Path,
) -> None:
    codec = AppliedSettingsCodec()
    encoded = codec.encode(
        python={"selector": "python3.12", "path": tmp_path / "venv", "owned": True},
        dependencies={
            "requirements.txt": ["httpx>=1", "token=do-not-store"],
            "requirements.lock": {"name": "httpx", "version": "1"},
        },
        managed_config={"http_port": 8069, "admin_passwd": "do-not-store"},
        addons=[tmp_path / "addons", tmp_path / "addons"],
        git={"ticket": "PROJ-123", "branch": "PROJ-123_1", "base": "main"},
    )
    assert "do-not-store" not in encoded
    decoded = codec.decode(encoded)
    components = decoded["components"]
    assert isinstance(components, dict)
    python = components["python"]
    assert isinstance(python, dict)
    assert python["path"] == str((tmp_path / "venv").resolve())
    dependencies = components["dependencies"]
    assert isinstance(dependencies, dict)
    inputs = dependencies["inputs"]
    assert isinstance(inputs, list)
    assert all(
        isinstance(item, dict) and set(item) == {"identity", "fingerprint"} for item in inputs
    )
    assert all("do-not-store" not in json.dumps(item) for item in inputs)
    assert components["addons"] == {
        "status": "known",
        "paths": [str((tmp_path / "addons").resolve())],
    }


def test_credential_bearing_dependency_identity_is_projected_before_storage_and_digest() -> None:
    codec = AppliedSettingsCodec()
    first = codec.decode(
        codec.encode(
            dependencies={"https://user:first-secret@example.invalid/requirements.txt": "httpx>=1"}
        )
    )
    second = codec.decode(
        codec.encode(
            dependencies={"https://user:second-secret@example.invalid/requirements.txt": "httpx>=1"}
        )
    )
    assert "first-secret" not in json.dumps(first)
    assert "second-secret" not in json.dumps(second)
    first_inputs = first["components"]["dependencies"]["inputs"]
    second_inputs = second["components"]["dependencies"]["inputs"]
    assert first_inputs == second_inputs


def test_all_string_components_round_trip_without_raw_secret() -> None:
    codec = AppliedSettingsCodec()
    encoded = codec.encode(
        python={"selector": "token=raw-secret", "path": "/tmp/token=raw-secret", "owned": True},
        dependencies={
            "https://user:raw-secret@example.invalid/requirements.txt": "token=raw-secret"
        },
        managed_config={"safe_value": "password=raw-secret"},
        addons=["/tmp/addons/token=raw-secret"],
        git={
            "ticket": "token=raw-secret",
            "branch": "branch/token=raw-secret",
            "base": "https://user:raw-secret@example.invalid/main",
        },
    )
    assert "raw-secret" not in encoded
    assert codec.decode(encoded)


@pytest.mark.parametrize(
    "credential",
    [
        "password=raw-secret",
        "passwd=raw-secret",
        "pwd=raw-secret",
        "secret=raw-secret",
        "token=raw-secret",
        "cookie=raw-secret",
        "jwt=raw-secret",
        "oauth=raw-secret",
        "api_key=raw-secret",
        "dsn=raw-secret",
        "database_url=raw-secret",
        "sentry_dsn=raw-secret",
        "docker_auth_config=raw-secret",
        "authorization=raw-secret",
        "bearer=raw-secret",
        "credential=raw-secret",
        "private_key=raw-secret",
        "access_key=raw-secret",
        "auth=raw-secret",
        "refresh=raw-secret",
        "client_secret=raw-secret",
    ],
)
def test_canonical_secret_vocabulary_is_projected_before_storage(
    credential: str,
) -> None:
    codec = AppliedSettingsCodec()
    encoded = codec.encode(
        python={"selector": credential, "path": "/tmp/venv", "owned": True},
        dependencies={credential: credential},
        managed_config={"safe_value": credential},
        addons=["/tmp/addons"],
        git={"ticket": credential, "branch": credential, "base": credential},
    )

    assert "raw-secret" not in encoded
    assert codec.decode(encoded)


def test_equivalent_addon_paths_are_deduplicated_after_canonicalization(tmp_path: Path) -> None:
    codec = AppliedSettingsCodec()
    first = tmp_path / "a" / ".." / "b"
    second = tmp_path / "b"
    decoded = codec.decode(codec.encode(addons=[first, second]))

    assert decoded["components"]["addons"] == {
        "status": "known",
        "paths": [str(second.resolve())],
    }


def test_codec_rejects_malformed_secret_bearing_and_unknown_versions() -> None:
    codec = AppliedSettingsCodec()
    with pytest.raises(AppliedSettingsError, match="unsupported"):
        codec.decode('{"version": 99, "components": {}}')
    with pytest.raises(AppliedSettingsError, match="malformed"):
        codec.decode('{"version": 1, "components": {}}')
    secret_document = json.loads(LEGACY_UNKNOWN_APPLIED_SETTINGS_JSON)
    secret_document["components"]["odoo"] = {
        "status": "known",
        "values": {"admin_passwd": "raw-secret"},
    }
    with pytest.raises(AppliedSettingsError, match="secret"):
        codec.decode(json.dumps(secret_document))


def test_codec_is_frozen_and_semantic_dependency_changes_change_fingerprint() -> None:
    codec = AppliedSettingsCodec()
    with pytest.raises(AttributeError):
        codec.version = 2  # type: ignore[misc]
    first = codec.decode(codec.encode(dependencies={"requirements.txt": ["a==1"]}))
    second = codec.decode(codec.encode(dependencies={"requirements.txt": ["a==2"]}))
    assert first["components"] != second["components"]
