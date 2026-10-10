from __future__ import annotations

import uuid

import pytest

from odoo_instance_sdk.exceptions import EnvironmentConflictError
from odoo_instance_sdk.internal.dbreplace_recovery import (
    CopyReplacementRecovery,
    decode_recovery,
    encode_recovery,
    recovery_from_row,
)


def _recovery() -> CopyReplacementRecovery:
    return CopyReplacementRecovery(
        version=1,
        environment_id=uuid.uuid4(),
        backup_id=uuid.uuid4(),
        previous_backup_id=uuid.uuid4(),
        target_database="copy_target",
        rollback_database="copy_target_odcli_rb_abc",
        filestore="copy_target",
        rollback_filestore="copy_target_odcli_rb_abc",
        cluster_id=str(uuid.uuid4()),
        data_directory="/owned/data",
        target_present=True,
        rollback_present=True,
        rollback_filestore_present=True,
    )


def test_recovery_codec_is_versioned_and_secret_free() -> None:
    value = _recovery()
    encoded = encode_recovery(value)

    assert decode_recovery(encoded) == value
    assert "password" not in encoded
    assert "secret" not in encoded


def test_structured_recovery_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError, match="replacement recovery is invalid"):
        decode_recovery('{"version":1,"unexpected":true}')


def test_legacy_recovery_is_available_only_as_explicit_repair_input() -> None:
    backup_id = uuid.uuid4()
    previous_backup_id = uuid.uuid4()
    retained = (
        f'{{"backup_id":"{backup_id}","previous_backup_id":"{previous_backup_id}",'
        '"target_database":"copy_target","rollback_database":"copy_target_odcli_rb_abc",'
        '"rollback_filestore":"copy_target_odcli_rb_abc","published":false,'
        '"target_present":true,"rollback_present":true,"rollback_filestore_present":true}'
        "; stale"
    )

    assert (
        recovery_from_row(
            {"last_error": "copy replacement cleanup_failed; retained=" + retained},
            allow_legacy=False,
        )
        is None
    )
    adopted = recovery_from_row(
        {"last_error": "copy replacement cleanup_failed; retained=" + retained},
        allow_legacy=True,
    )
    assert adopted is not None
    assert isinstance(adopted, dict)
    assert adopted["backup_id"] == str(backup_id)


def test_legacy_recovery_rejects_secret_bearing_text() -> None:
    value = (
        f'copy replacement cleanup_failed; retained={{"backup_id":"{uuid.uuid4()}",'
        f'"previous_backup_id":"{uuid.uuid4()}","target_database":"copy_target",'
        '"rollback_database":"copy_target_odcli_rb_abc"}; password=secret'
    )

    assert recovery_from_row({"last_error": value}, allow_legacy=True) is None


def test_malformed_structured_recovery_fails_closed() -> None:
    with pytest.raises(EnvironmentConflictError, match="replacement recovery is invalid"):
        recovery_from_row({"recovery_json": "not-json"})
