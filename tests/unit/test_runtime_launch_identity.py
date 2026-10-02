from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from odoo_instance_sdk.internal.proc import PreparedStep
from odoo_instance_sdk.resources.instance.runtime import (
    _decode_launch_identity,
    _encode_launch_identity,
    _project_runtime_argv,
    _runtime_argv_mismatch_components,
    _RuntimeIdentity,
)


def _step(tmp_path: Path) -> PreparedStep:
    return PreparedStep(
        step_id="instance.detached",
        argv=(
            sys.executable,
            str(tmp_path / "odoo-bin"),
            "--database",
            "database",
            "--db_password",
            "super-secret",
            "--config",
            str(tmp_path / "odoo.conf"),
        ),
        cwd=str(tmp_path),
    )


def test_launch_identity_is_secret_free_and_round_trips(tmp_path: Path) -> None:
    encoded = _encode_launch_identity(_step(tmp_path), executable_prefix_length=2)
    assert "super-secret" not in encoded
    identity = _decode_launch_identity(encoded)

    assert identity.schema_version == 1
    assert identity.argv[4:6] == ("--db_password", "<redacted>")
    assert identity.sensitive_argv_indices == (4, 5)
    assert identity.executable_prefix_length == 2
    assert identity.config_path == str((tmp_path / "odoo.conf").resolve())


def test_live_secret_changes_are_redacted_without_value_comparison(tmp_path: Path) -> None:
    encoded = _encode_launch_identity(_step(tmp_path), executable_prefix_length=2)
    identity = _decode_launch_identity(encoded)
    live = list(_step(tmp_path).argv)
    live[5] = "another-secret"

    assert (
        _project_runtime_argv(
            live,
            sensitive_argv_indices=identity.sensitive_argv_indices,
            executable_prefix_length=identity.executable_prefix_length,
        )
        == identity.argv
    )


def test_mismatch_components_are_safe_and_deterministic(tmp_path: Path) -> None:
    encoded = _decode_launch_identity(
        _encode_launch_identity(_step(tmp_path), executable_prefix_length=2)
    )
    live = list(encoded.argv)
    live[3] = "other-database"
    identity = cast(
        "_RuntimeIdentity",
        SimpleNamespace(
            expected_argv=encoded.argv,
            expected_executable_prefix_length=encoded.executable_prefix_length,
            live_argv=tuple(live),
        ),
    )
    assert _runtime_argv_mismatch_components(identity) == ("argv: --database",)
    assert "other-database" not in ", ".join(_runtime_argv_mismatch_components(identity))


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (None, "captured launch identity unavailable"),
        ('{"schema_version": 2}', "captured launch identity unreadable"),
    ],
)
def test_missing_or_unsupported_identity_fails_closed(raw: str | None, message: str) -> None:
    with pytest.raises(RuntimeError, match=message):
        _decode_launch_identity(raw)


def test_raw_secret_in_persisted_protected_binding_is_rejected(tmp_path: Path) -> None:
    document = json.loads(_encode_launch_identity(_step(tmp_path), executable_prefix_length=2))
    document["argv"][5] = "super-secret"
    document["sensitive_argv_indices"] = []
    with pytest.raises(RuntimeError, match="captured launch identity unreadable"):
        _decode_launch_identity(json.dumps(document))
