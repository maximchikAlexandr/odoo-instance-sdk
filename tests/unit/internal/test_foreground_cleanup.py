from __future__ import annotations

from unittest.mock import Mock

import pytest

from odoo_instance_sdk.internal.server import wait_foreground_process_with_cleanup


def test_wait_cleanup_failure_preserves_original_wait_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc = Mock()
    proc.pid = 12345
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.server.wait_foreground_process",
        Mock(side_effect=RuntimeError("original wait error")),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.server.terminate_foreground_process",
        Mock(side_effect=OSError("cleanup failed")),
    )

    with pytest.raises(RuntimeError, match="original wait error"):
        wait_foreground_process_with_cleanup(proc)
