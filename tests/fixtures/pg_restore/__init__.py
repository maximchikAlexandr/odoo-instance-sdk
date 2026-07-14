"""pg_restore fixture helpers."""

from pathlib import Path

FIXTURES: dict[str, str] = {
    "pg_restore_exit0": "#!/bin/sh\nexit 0\n",
    "pg_restore_exit1": "#!/bin/sh\necho 'invalid backup' >&2\nexit 1\n",
    "pg_restore_timeout": "#!/bin/sh\nsleep 10\n",
}


def write_fixtures(dst_dir: Path) -> dict[str, Path]:
    dst_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Path] = {}
    for name, content in FIXTURES.items():
        path = dst_dir / name
        path.write_text(content)
        path.chmod(0o755)
        result[name] = path
    return result
