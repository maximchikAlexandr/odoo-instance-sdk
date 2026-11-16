"""Odoo shell source for one mail configuration transaction."""

from __future__ import annotations

ODOO_MAIL_SCRIPT = """
import json
import os
import socket
import sys
from pathlib import Path

request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))


def _write_private(path, text):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(descriptor, text.encode("utf-8"))
    finally:
        os.close(descriptor)


def _probe(host, port):
    try:
        connection = socket.create_connection((host, int(port)), timeout=5)
    except TimeoutError as exc:
        raise RuntimeError("mail_timeout") from exc
    except OSError as exc:
        raise RuntimeError("mail_connection") from exc
    connection.close()


def _xmlid(name):
    return env["ir.model.data"].search(
        [("module", "=", "odcli_mail"), ("name", "=", name)], limit=1
    )


def _inbound_model():
    for name in ("discuss.channel", "mail.channel"):
        found = env["ir.model"].search([("model", "=", name)], limit=1)
        if found:
            return found
    return None


def _snapshot(path):
    mail_servers = [
        {"id": row.id, "active": bool(row.active)}
        for row in env["ir.mail_server"].with_context(active_test=False).search([])
    ]
    fetchmail_servers = [
        {"id": row.id, "active": bool(row.active)}
        for row in env["fetchmail.server"].with_context(active_test=False).search([])
    ]
    _write_private(
        path,
        json.dumps(
            {"mail_servers": mail_servers, "fetchmail_servers": fetchmail_servers},
            sort_keys=True,
        ),
    )


def _deactivate(model_name, kept_id):
    for row in env[model_name].with_context(active_test=False).search([]):
        if row.id != kept_id and row.active:
            row.write({"active": False})


def _upsert(model_name, xml_name, values):
    data = _xmlid(xml_name)
    if data:
        server = env[model_name].browse(data.res_id).exists()
        if server:
            server.write(values)
            return server
        data.unlink()
    server = env[model_name].create(values)
    env["ir.model.data"].create(
        {
            "module": "odcli_mail",
            "name": xml_name,
            "model": model_name,
            "res_id": server.id,
            "noupdate": True,
        }
    )
    return server


def _configure():
    if request.get("probe"):
        _probe(request["smtp_host"], request["smtp_port"])
        _probe(request["imap_host"], request["imap_port"])
    fetchmail = env["ir.module.module"].search(
        [("name", "=", "fetchmail"), ("state", "=", "installed")], limit=1
    )
    if not fetchmail:
        raise RuntimeError("mail_fetchmail_missing")
    inbound = _inbound_model()
    if not inbound:
        raise RuntimeError("mail_inbound_model_missing")
    secret = json.loads(Path(request["credentials_path"]).read_text(encoding="utf-8"))["password"]
    if not isinstance(secret, str) or not secret:
        raise RuntimeError("mail_credentials_missing")
    snapshot = Path(request["snapshot_path"])
    if not snapshot.exists() or not _xmlid("greenmail_smtp"):
        _snapshot(snapshot)
    mail_fields = env["ir.mail_server"]._fields
    mail_values = {
        "name": "OdCLI GreenMail",
        "active": True,
        "sequence": 1,
        "smtp_host": request["smtp_host"],
        "smtp_port": int(request["smtp_port"]),
        "smtp_encryption": "none",
        "smtp_user": request["mailbox"],
        "smtp_pass": secret,
    }
    if "from_filter" in mail_fields:
        mail_values["from_filter"] = False
    if "smtp_authentication" in mail_fields:
        mail_values["smtp_authentication"] = "login"
    if "smtp_debug" in mail_fields:
        mail_values["smtp_debug"] = False
    server = _upsert("ir.mail_server", "greenmail_smtp", mail_values)
    _deactivate("ir.mail_server", server.id)
    fetch_server = _upsert(
        "fetchmail.server",
        "greenmail_imap",
        {
            "name": "OdCLI GreenMail",
            "active": True,
            "state": "done",
            "server_type": "imap",
            "is_ssl": False,
            "server": request["imap_host"],
            "port": int(request["imap_port"]),
            "user": request["mailbox"],
            "password": secret,
            "object_id": inbound.id,
        },
    )
    _deactivate("fetchmail.server", fetch_server.id)
    return {
        "database": request["database"],
        "mailbox": request["mailbox"],
        "placement": request["placement"],
    }


def _restore(model_name, rows):
    for row in rows:
        record = env[model_name].browse(row["id"]).exists()
        if record:
            record.write({"active": bool(row["active"])})


def _remove_managed(xml_name, model_name):
    data = _xmlid(xml_name)
    if not data:
        return
    record = env[model_name].browse(data.res_id).exists()
    if record:
        record.unlink()
    data.unlink()


def _undo():
    snapshot = Path(request["snapshot_path"])
    if not snapshot.exists():
        raise RuntimeError("mail_snapshot_missing")
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    _restore("ir.mail_server", payload["mail_servers"])
    _restore("fetchmail.server", payload["fetchmail_servers"])
    _remove_managed("greenmail_smtp", "ir.mail_server")
    _remove_managed("greenmail_imap", "fetchmail.server")
    return {"database": request["database"], "undone": True}


if request["operation"] == "configure":
    result = _configure()
elif request["operation"] == "undo":
    result = _undo()
else:
    raise RuntimeError("mail_configure_failed")
"""
