from __future__ import annotations

import io
import shutil
import zipfile
from collections.abc import Callable
from copy import copy
from pathlib import Path

import pytest

from odoo_instance_sdk.internal.backup_validation import (
    BACKUP_CORRUPT,
    BACKUP_INSUFFICIENT_DISK,
    BACKUP_OPERATOR_LIMIT,
    BACKUP_UNSAFE,
    enforce_operator_uncompressed_limit,
    preflight_restore_disk_space,
    read_operator_max_uncompressed_bytes,
    validate_dump,
    validate_zip,
)


class TestZipValidation:
    def test_valid_zip(self, backup_fixtures: dict[str, Path]) -> None:
        result = validate_zip(backup_fixtures["valid.zip"])
        assert result.valid is True
        assert result.db_name == "testdb"

    def test_large_odoo_dump_has_no_arbitrary_size_ceiling(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dump = zipfile.ZipInfo("dump.sql")
        dump.file_size = 100 * 1024**3
        dump.compress_size = 2 * 1024**3
        dump.compress_type = zipfile.ZIP_DEFLATED
        manifest = zipfile.ZipInfo("manifest.json")
        manifest.file_size = 64
        manifest.compress_size = 64
        manifest.compress_type = zipfile.ZIP_DEFLATED

        class LargeBackup:
            def __enter__(self) -> LargeBackup:
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def infolist(self) -> list[zipfile.ZipInfo]:
                return [dump, manifest]

            def open(self, name: str | zipfile.ZipInfo, *_args: object) -> io.BytesIO:
                resolved = name if isinstance(name, str) else name.filename
                if resolved == "manifest.json":
                    return io.BytesIO(
                        b'{"db_name":"large","version":"13.0","major_version":"13.0"}'
                    )
                return io.BytesIO(b"")

        monkeypatch.setattr(zipfile, "is_zipfile", lambda _path: True)
        monkeypatch.setattr(zipfile, "ZipFile", lambda _path: LargeBackup())
        disk_usage_type = type(shutil.disk_usage("."))
        monkeypatch.setattr(
            shutil,
            "disk_usage",
            lambda _path: disk_usage_type(10**12, 0, 500 * 1024**3),
        )

        result = validate_zip(tmp_path / "large.zip")

        assert result.valid is True
        assert result.db_version == "13.0"
        assert result.uncompressed_bytes > 100_000_000_000

    def test_missing_member(self, backup_fixtures: dict[str, Path]) -> None:
        result = validate_zip(backup_fixtures["missing_member.zip"])
        assert result.valid is False
        assert any("dump.sql" in e for e in result.errors)

    def test_invalid_manifest(self, backup_fixtures: dict[str, Path]) -> None:
        result = validate_zip(backup_fixtures["invalid_manifest.zip"])
        assert result.valid is False

    def test_corrupted_zip(self, backup_fixtures: dict[str, Path]) -> None:
        result = validate_zip(backup_fixtures["corrupted.zip"])
        assert result.valid is False
        assert len(result.errors) > 0
        assert result.error_code == BACKUP_CORRUPT

    def test_not_a_zip(self, tmp_path: Path) -> None:
        f = tmp_path / "not.zip"
        f.write_text("not a zip file")
        result = validate_zip(f)
        assert result.valid is False
        assert "Not a valid ZIP" in str(result.errors)
        assert result.error_code == BACKUP_CORRUPT


@pytest.mark.parametrize(
    ("version", "major_version", "expected"),
    [
        ("13.0", "13", "13.0"),
        (None, "13", "13.0"),
        (None, 13, "13.0"),
        ("19.0", "19", "19.0"),
        (None, "19", "19.0"),
        ("", "13", "13.0"),
    ],
    ids=[
        "explicit-version",
        "major-only-string",
        "major-only-int",
        "v19",
        "v19-major",
        "empty-version",
    ],
)
def test_manifest_version_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    version: str | None,
    major_version: str | int,
    expected: str,
) -> None:
    manifest_blob = '{"db_name":"db","major_version":' + (
        f'"{major_version}"' if isinstance(major_version, str) else f"{major_version}"
    )
    if version is not None:
        manifest_blob += f',"version":"{version}"'
    manifest_blob += "}"

    def _info(name: str, size: int) -> zipfile.ZipInfo:
        mi = zipfile.ZipInfo(name)
        mi.file_size = size
        mi.compress_size = size
        return mi

    dump = _info("dump.sql", 4)
    manifest = _info("manifest.json", len(manifest_blob))

    class FakeZip:
        def __enter__(self) -> FakeZip:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def infolist(self) -> list[zipfile.ZipInfo]:
            return [manifest, dump]

        def open(self, name: str | zipfile.ZipInfo, *_args: object) -> io.BytesIO:
            resolved = name if isinstance(name, str) else name.filename
            if resolved == "manifest.json":
                return io.BytesIO(manifest_blob.encode("utf-8"))
            return io.BytesIO(b"x" * dump.file_size)

    monkeypatch.setattr(zipfile, "is_zipfile", lambda _path: True)
    monkeypatch.setattr(zipfile, "ZipFile", lambda _path: FakeZip())

    result = validate_zip(tmp_path / "manifest.zip")
    assert result.valid is True
    assert result.db_version == expected


def _write_odoo_zip(
    path: Path,
    *,
    dump_bytes: bytes,
    manifest: dict[str, object],
    extra_members: dict[str, bytes] | None = None,
) -> Path:
    import json

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("dump.sql", dump_bytes)
        for name, payload in (extra_members or {}).items():
            zf.writestr(name, payload)
    return path


@pytest.mark.parametrize(
    ("dump_uncompressed", "total_uncompressed", "label"),
    [
        (1210 * 1024**2, 1530 * 1024**2, "1.21 GiB dump 1.53 GiB total"),
        (100 * 1024**3, 102 * 1024**3, "100 GiB dump modelled by metadata"),
    ],
    ids=["1.21gb-fixture", "100gb-metadata-model"],
)
def test_large_dump_passes_without_byte_ceiling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dump_uncompressed: int,
    total_uncompressed: int,
    label: str,
) -> None:
    dump = zipfile.ZipInfo("dump.sql")
    dump.file_size = dump_uncompressed
    dump.compress_size = max(1, dump_uncompressed // 50)
    dump.compress_type = zipfile.ZIP_DEFLATED
    filestore_size = total_uncompressed - dump_uncompressed
    filestore = zipfile.ZipInfo("filestore/db/asset.bin")
    filestore.file_size = filestore_size
    filestore.compress_size = max(1, filestore_size // 50)
    filestore.compress_type = zipfile.ZIP_DEFLATED
    manifest = zipfile.ZipInfo("manifest.json")
    manifest.file_size = 48
    manifest.compress_size = 48
    manifest.compress_type = zipfile.ZIP_DEFLATED
    manifest_blob = b'{"db_name":"db","version":"19.0","major_version":"19"}'

    class MetadataZip:
        def __enter__(self) -> MetadataZip:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def infolist(self) -> list[zipfile.ZipInfo]:
            return [manifest, dump, filestore]

        def open(self, name: str | zipfile.ZipInfo, *_args: object) -> io.BytesIO:
            resolved = name if isinstance(name, str) else name.filename
            if resolved == "manifest.json":
                return io.BytesIO(manifest_blob)
            return io.BytesIO(b"")

    monkeypatch.setattr(zipfile, "is_zipfile", lambda _path: True)
    monkeypatch.setattr(zipfile, "ZipFile", lambda _path: MetadataZip())

    result = validate_zip(tmp_path / f"{label}.zip")

    assert result.valid is True, result.errors
    assert result.uncompressed_bytes == total_uncompressed + manifest.file_size
    assert result.error_code is None


@pytest.mark.parametrize(
    ("kind", "build"),
    [
        (
            "path_traversal",
            lambda tmp: (
                _write_odoo_zip(
                    tmp / "traversal.zip",
                    dump_bytes=b"x",
                    manifest={"db_name": "db"},
                    extra_members={"../escape.sql": b"y"},
                ),
                None,
            ),
        ),
        ("duplicate", lambda tmp: _build_duplicate(tmp / "dup.zip")),
        (
            "encrypted",
            lambda tmp: _build_member_variant(tmp / "enc.zip", flag=0x1),
        ),
        (
            "unsupported",
            lambda tmp: _build_member_variant(tmp / "unsupported.zip", compress=99),
        ),
        (
            "zip_bomb_ratio",
            lambda tmp: (
                _write_odoo_zip(
                    tmp / "bomb.zip",
                    dump_bytes=b"x",
                    manifest={"db_name": "db"},
                    extra_members={"filestore/db/big.bin": b"\x00" * 4},
                ),
                None,
            ),
        ),
    ],
    ids=["path-traversal", "duplicate", "encrypted", "unsupported", "zip-bomb-ratio"],
)
def test_unsafe_archive_rejects_with_backup_unsafe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    build: Callable[[Path], tuple[Path, Callable[[zipfile.ZipFile], list[zipfile.ZipInfo]] | None]],
) -> None:
    archive, mutator = build(tmp_path)
    if mutator is not None:
        monkeypatch.setattr(zipfile.ZipFile, "infolist", mutator)
    elif kind == "zip_bomb_ratio":

        def inflated(archive_obj: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
            infos = [copy(i) for i in _original_infolist(archive_obj)]
            for info in infos:
                if info.filename.endswith("big.bin"):
                    info.file_size = 10 * 1024**2
                    info.compress_size = 1
            return infos

        monkeypatch.setattr(zipfile.ZipFile, "infolist", inflated)
    result = validate_zip(archive)
    assert result.valid is False
    assert result.error_code == BACKUP_UNSAFE


_original_infolist = zipfile.ZipFile.infolist


def _build_duplicate(path: Path) -> tuple[Path, None]:
    import warnings

    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", '{"db_name":"db"}')
        zf.writestr("dump.sql", "select 1;\n")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            zf.writestr("dump.sql", "select 2;\n")
    return path, None


def _build_member_variant(
    path: Path, *, flag: int = 0, compress: int = 0
) -> tuple[Path, Callable[[zipfile.ZipFile], list[zipfile.ZipInfo]]]:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", '{"db_name":"db"}')
        zf.writestr("dump.sql", "select 1;\n")
        zf.writestr("filestore/db/asset.bin", b"payload")

    def mutator(archive_obj: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
        infos = [copy(i) for i in _original_infolist(archive_obj)]
        for info in infos:
            if info.filename == "filestore/db/asset.bin":
                if flag:
                    info.flag_bits |= flag
                if compress:
                    info.compress_type = compress
        return infos

    return path, mutator


def test_dump_sql_high_ratio_is_not_unsafe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dump = zipfile.ZipInfo("dump.sql")
    dump.file_size = 200 * 1024
    dump.compress_size = 1
    dump.compress_type = zipfile.ZIP_DEFLATED
    manifest = zipfile.ZipInfo("manifest.json")
    manifest.file_size = 36
    manifest.compress_size = 36
    manifest.compress_type = zipfile.ZIP_DEFLATED
    manifest_blob = b'{"db_name":"db","version":"19.0","major_version":"19"}'

    class HighRatioZip:
        def __enter__(self) -> HighRatioZip:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def infolist(self) -> list[zipfile.ZipInfo]:
            return [manifest, dump]

        def open(self, name: str | zipfile.ZipInfo, *_args: object) -> io.BytesIO:
            resolved = name if isinstance(name, str) else name.filename
            if resolved == "manifest.json":
                return io.BytesIO(manifest_blob)
            return io.BytesIO(b"\x00" * dump.file_size)

    monkeypatch.setattr(zipfile, "is_zipfile", lambda _path: True)
    monkeypatch.setattr(zipfile, "ZipFile", lambda _path: HighRatioZip())

    result = validate_zip(tmp_path / "high-ratio-dump.zip")
    assert result.valid is True, result.errors


def test_insufficient_disk_preflight_returns_resource_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(
        shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=512 * 1024**2),
    )
    result = preflight_restore_disk_space(10 * 1024**3, tmp_path)
    assert result.ok is False
    assert result.error_code == BACKUP_INSUFFICIENT_DISK
    assert result.measured_bytes == 10 * 1024**3
    assert result.available_bytes == 512 * 1024**2
    assert result.reserve_bytes == max(1024**3, int(512 * 1024**2 * 0.10))


def test_insufficient_disk_preflight_uses_data_dir_when_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[Path] = []

    def fake_disk_usage(path: Path) -> object:
        from types import SimpleNamespace

        captured.append(path)
        return SimpleNamespace(free=2 * 1024**3)

    monkeypatch.setattr(shutil, "disk_usage", fake_disk_usage)
    data_dir = tmp_path / "filestore"
    data_dir.mkdir()
    result = preflight_restore_disk_space(500 * 1024**2, data_dir)
    assert result.ok is True
    assert captured and captured[-1] == data_dir.resolve()


def test_operator_limit_reads_user_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_root = tmp_path / "config"
    config_root.mkdir()
    (config_root / "user.toml").write_text("[backup]\nmax_uncompressed_bytes = 1073741824\n")
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_config_root",
        lambda **_kwargs: config_root,
    )
    allowed, source = read_operator_max_uncompressed_bytes()
    assert allowed == 1024**3
    assert source == "user.toml"

    below = enforce_operator_uncompressed_limit(500 * 1024**2)
    assert below.ok is True
    assert below.allowed_bytes == 1024**3

    above = enforce_operator_uncompressed_limit(2 * 1024**3)
    assert above.ok is False
    assert above.error_code == BACKUP_OPERATOR_LIMIT
    assert above.allowed_bytes == 1024**3
    assert above.measured_bytes == 2 * 1024**3
    assert above.limit_source == "user.toml"


def test_operator_limit_absent_means_no_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_config_root",
        lambda **_kwargs: tmp_path,
    )
    allowed, source = read_operator_max_uncompressed_bytes()
    assert allowed is None
    assert source == "unset"
    result = enforce_operator_uncompressed_limit(10 * 1024**3)
    assert result.ok is True
    assert result.limit_source == "unset"


class _CountingReader:
    def __init__(self, data: bytes, chunk_sizes: list[int]) -> None:
        self._data = data
        self._chunk_sizes = chunk_sizes
        self._pos = 0

    def __enter__(self) -> _CountingReader:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        if size <= 0:
            size = len(self._data) - self._pos
        chunk = self._data[self._pos : self._pos + size]
        self._pos += len(chunk)
        self._chunk_sizes.append(size)
        return chunk


class _BoundedZip:
    payload = b"abcdef" * 100
    manifest_blob = b'{"db_name":"db","version":"19.0","major_version":"19"}'

    def __init__(self, chunk_sizes: list[int]) -> None:
        self._chunk_sizes = chunk_sizes

    def __enter__(self) -> _BoundedZip:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def infolist(self) -> list[zipfile.ZipInfo]:
        dump = zipfile.ZipInfo("dump.sql")
        dump.file_size = len(self.payload)
        dump.compress_size = len(self.payload)
        manifest = zipfile.ZipInfo("manifest.json")
        manifest.file_size = len(self.manifest_blob)
        manifest.compress_size = len(self.manifest_blob)
        return [manifest, dump]

    def open(self, name: str | zipfile.ZipInfo, *_args: object) -> _CountingReader:
        resolved = name if isinstance(name, str) else name.filename
        data = self.manifest_blob if resolved == "manifest.json" else self.payload
        return _CountingReader(data, self._chunk_sizes)


def test_bounded_memory_streaming_uses_65536_buffer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunk_sizes: list[int] = []

    monkeypatch.setattr(zipfile, "is_zipfile", lambda _path: True)
    monkeypatch.setattr(zipfile, "ZipFile", lambda _path: _BoundedZip(chunk_sizes))

    result = validate_zip(tmp_path / "bounded.zip")
    assert result.valid is True
    assert all(size <= 65536 for size in chunk_sizes)


class TestDumpValidation:
    def test_unavailable_when_pg_restore_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.backup_validation.shutil.which",
            lambda *a, **k: None,
        )
        result = validate_dump(Path("/nonexistent"), raise_if_unavailable=False)
        assert result.unavailable is True

    def test_raise_if_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.backup_validation.shutil.which",
            lambda *a, **k: None,
        )
        from odoo_instance_sdk.exceptions import BackupValidationUnavailableError

        with pytest.raises(BackupValidationUnavailableError):
            validate_dump(Path("/nonexistent"), raise_if_unavailable=True)

    def test_valid_dump_with_fake_pg_restore(
        self, pg_restore_fixtures: dict[str, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.backup_validation.shutil.which",
            lambda *a, **k: str(pg_restore_fixtures["pg_restore_exit0"]),
        )
        result = validate_dump(
            Path("/nonexistent"),
            timeout=5.0,
        )
        assert result.valid is True

    def test_failing_pg_restore(
        self, pg_restore_fixtures: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        f = tmp_path / "dummy.zip"
        f.write_text("not a backup")
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.backup_validation.shutil.which",
            lambda *a, **k: str(pg_restore_fixtures["pg_restore_exit1"]),
        )
        result = validate_dump(f, timeout=5.0)
        assert result.valid is False

    def test_dump_timeout(
        self, pg_restore_fixtures: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        f = tmp_path / "dummy.zip"
        f.write_text("not a backup")
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.backup_validation.shutil.which",
            lambda *a, **k: str(pg_restore_fixtures["pg_restore_timeout"]),
        )
        result = validate_dump(f, timeout=1.0)
        assert result.valid is False
        assert any("timed out" in e for e in result.errors)
