"""HTTP fixtures for Odoo database server behavior."""

import io
import zipfile


def _make_backup_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", '{"db_name": "testdb", "db_version": "19.0"}')
        zf.writestr("dump.sql", "-- empty dump")
    buf.seek(0)
    return buf.getvalue()


BACKUP_ZIP_CONTENT = _make_backup_zip()
