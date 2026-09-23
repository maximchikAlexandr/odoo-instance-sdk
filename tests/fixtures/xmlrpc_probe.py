"""Test-support XML-RPC helper for real Odoo E2E probes."""

from __future__ import annotations

import base64
import xmlrpc.client
from typing import Any, cast

_PROBE_MARKER = "ODCLI-E2E-RESTORED"


def xmlrpc_probe(base_url: str, database: str) -> tuple[str, bytes]:
    """Verify restored database state through XML-RPC without inline ``ServerProxy``."""
    common = xmlrpc.client.ServerProxy(f"{base_url}/xmlrpc/2/common", allow_none=True)
    uid = common.authenticate(database, "admin", "admin", {})
    assert isinstance(uid, int) and uid > 0
    models = xmlrpc.client.ServerProxy(f"{base_url}/xmlrpc/2/object", allow_none=True)
    records = cast(
        "list[dict[str, Any]]",
        models.execute_kw(
            database,
            uid,
            "admin",
            "odcli.e2e.probe",
            "search_read",
            [[("marker", "=", _PROBE_MARKER)]],
            {"fields": ["name", "marker"], "limit": 1},
        ),
    )
    assert len(records) == 1
    assert records[0]["name"] == "Pinned Odoo 19 fixture"
    assert records[0]["marker"] == _PROBE_MARKER
    attachments = cast(
        "list[dict[str, Any]]",
        models.execute_kw(
            database,
            uid,
            "admin",
            "ir.attachment",
            "search_read",
            [[("name", "=", "odcli-e2e-attachment.txt"), ("res_model", "=", "odcli.e2e.probe")]],
            {"fields": ["datas", "store_fname"], "limit": 1},
        ),
    )
    assert len(attachments) == 1 and attachments[0]["store_fname"]
    encoded = attachments[0]["datas"]
    if isinstance(encoded, xmlrpc.client.Binary):
        encoded = bytes(encoded.data)
    assert isinstance(encoded, (str, bytes))
    return str(records[0]["name"]), base64.b64decode(encoded)


__all__ = ["xmlrpc_probe"]
