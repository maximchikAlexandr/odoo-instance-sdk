"""Backup fixture helpers."""

import io
import zipfile
from pathlib import Path


def _make_valid_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", '{"db_name": "testdb", "db_version": "19.0"}')
        zf.writestr("dump.sql", "CREATE TABLE test (id INT);")
    buf.seek(0)
    return buf.getvalue()


def _make_missing_member_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", '{"db_name": "testdb"}')
    buf.seek(0)
    return buf.getvalue()


def _make_invalid_manifest_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", "not valid json")
        zf.writestr("dump.sql", "CREATE TABLE test (id INT);")
    buf.seek(0)
    return buf.getvalue()


def _make_corrupted_zip() -> bytes:
    data = bytearray(_make_valid_zip())
    data[len(data) // 2] = 0xFF
    return bytes(data)


FIXTURES: dict[str, bytes] = {
    "valid.zip": _make_valid_zip(),
    "missing_member.zip": _make_missing_member_zip(),
    "invalid_manifest.zip": _make_invalid_manifest_zip(),
    "corrupted.zip": _make_corrupted_zip(),
}


def write_fixtures(dst_dir: Path) -> dict[str, Path]:
    dst_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Path] = {}
    for name, content in FIXTURES.items():
        path = dst_dir / name
        path.write_bytes(content)
        result[name] = path
    return result
