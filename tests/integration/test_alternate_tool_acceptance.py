from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import UpdateError
from odoo_instance_sdk.internal.alternate_tool import (
    REPOSITORY_URL,
    AlternateIdentity,
    AlternateManifest,
    identity,
    remove_alternate_tool,
    render_launcher,
)
from odoo_instance_sdk.internal.self_update import assert_direct_update_allowed
from odoo_instance_sdk.internal.storage_migration import migrate_storage

pytestmark = pytest.mark.integration

_SHA_BY_LABEL = {
    "canonical": "1" * 40,
    "numbered-1": "2" * 40,
    "numbered-2": "3" * 40,
    "hotfix-42": "4" * 40,
    "hotfix-43": "5" * 40,
}


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        return "<absent>"
    for path in sorted(root.rglob("*")):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        if path.is_symlink():
            digest.update(b"symlink:")
            digest.update(os.readlink(path).encode("utf-8"))
        elif path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _write_revision(executable: Path, column: str) -> None:
    source = f"""#!{sys.executable}
import os
import sqlite3
import sys
import time
from pathlib import Path

label = sys.argv[1]
root = Path(os.environ.get("ODCLI_USER_ROOT", Path.home() / ".odcli")).resolve()
root.mkdir(mode=0o700, parents=True, exist_ok=True)
barrier = Path(os.environ["ODCLI_ACCEPTANCE_BARRIER"])
(root / "ready").write_text(label, encoding="utf-8")
while not barrier.exists():
    time.sleep(0.01)
with sqlite3.connect(root / "catalog.sqlite3") as connection:
    connection.execute(
        "CREATE TABLE schema_state (revision TEXT PRIMARY KEY, {column} TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO schema_state (revision, {column}) VALUES (?, ?)",
        (label, label),
    )
    connection.commit()
(root / "access.log").write_text(label, encoding="utf-8")
time.sleep(0.1)
"""
    executable.write_text(source, encoding="utf-8")
    executable.chmod(0o700)


def _publish_fixture(
    identity_value: AlternateIdentity,
    *,
    sha: str,
    column: str,
    pr: int | None = None,
) -> AlternateManifest:
    identity_value.tool_root.mkdir(mode=0o700, parents=True)
    identity_value.tool_bin.mkdir(mode=0o700, parents=True)
    _write_revision(identity_value.executable, column)
    initial = AlternateManifest(
        identity_value,
        sha,
        REPOSITORY_URL,
        identity_value.executable,
        "",
        pr=pr,
        review_sha=sha if pr is not None else None,
        review_session="acceptance" if pr is not None else None,
    )
    shim = render_launcher(initial)
    manifest = AlternateManifest(
        identity_value,
        sha,
        REPOSITORY_URL,
        identity_value.executable,
        shim,
        pr=initial.pr,
        review_sha=initial.review_sha,
        review_session=initial.review_session,
    )
    identity_value.launcher.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    identity_value.launcher.write_text(shim, encoding="utf-8")
    identity_value.launcher.chmod(0o700)
    identity_value.manifest.parent.mkdir(mode=0o700, exist_ok=True)
    identity_value.manifest.write_text(
        json.dumps(manifest.to_json(), sort_keys=True) + "\n", encoding="utf-8"
    )
    identity_value.manifest.chmod(0o600)
    return manifest


def _assert_schema(root: Path, column: str, label: str) -> None:
    with sqlite3.connect(root / "catalog.sqlite3") as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(schema_state)")]
        rows = connection.execute(f"SELECT revision, {column} FROM schema_state").fetchall()
    assert columns == ["revision", column]
    assert rows == [(label, label)]


def test_incompatible_revision_migrations_are_confined_to_selected_roots(  # noqa: C901
    tmp_path: Path,
) -> None:
    """Run five incompatible catalogue schemas through the shared launcher boundary."""
    home = tmp_path / "home"
    home.mkdir()
    canonical = home / ".odcli"
    canonical.mkdir(mode=0o700)
    canonical_sentinel = canonical / "sentinel"
    canonical_sentinel.write_text("canonical", encoding="utf-8")

    identities = {
        "numbered-1": identity("numbered", "1", home=home),
        "numbered-2": identity("numbered", "2", home=home),
        "hotfix-42": identity("hotfix", "42", home=home),
        "hotfix-43": identity("hotfix", "43", home=home),
    }
    columns = {
        "canonical": "canonical_payload",
        "numbered-1": "revision_one_payload",
        "numbered-2": "revision_two_payload",
        "hotfix-42": "hotfix_42_payload",
        "hotfix-43": "hotfix_43_payload",
    }
    for label, identity_value in identities.items():
        _publish_fixture(
            identity_value,
            sha=_SHA_BY_LABEL[label],
            column=columns[label],
            pr=int(label.rsplit("-", 1)[1]) if label.startswith("hotfix") else None,
        )
    canonical_executable = tmp_path / "canonical-odcli"
    _write_revision(canonical_executable, columns["canonical"])

    barrier = tmp_path / "release-migrations"
    environment = dict(os.environ)
    environment.update({"HOME": str(home), "ODCLI_ACCEPTANCE_BARRIER": str(barrier)})
    for name in ("ODCLI_USER_ROOT", "ODCLI_LAUNCHER_KIND", "ODCLI_LAUNCHER_ID"):
        environment.pop(name, None)

    processes: list[tuple[str, Path, subprocess.Popen[str]]] = []
    canonical_process = subprocess.Popen(
        [str(canonical_executable), "canonical"],
        cwd=tmp_path,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    processes.append(("canonical", canonical, canonical_process))
    for label, identity_value in identities.items():
        process = subprocess.Popen(
            [str(identity_value.launcher), label],
            cwd=tmp_path,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        processes.append((label, identity_value.state_root, process))

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if all((root / "ready").exists() for _label, root, _process in processes):
            break
        time.sleep(0.01)
    else:
        details: list[object] = []
        for label, _root, process in processes:
            if process.poll() is None:
                details.append((label, "running"))
                continue
            stdout, stderr = process.communicate(timeout=1)
            details.append((label, process.returncode, stdout, stderr))
        pytest.fail(f"revision processes did not reach the migration barrier: {details}")
    barrier.write_text("go", encoding="utf-8")

    for label, root, process in processes:
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, f"{label}: stdout={stdout!r} stderr={stderr!r}"
        assert stdout == ""
        if label.startswith("hotfix"):
            assert f"odcli-fix-{label.rsplit('-', 1)[1]}" in stderr
        else:
            assert stderr == ""
        _assert_schema(root, columns[label], label)
        assert (root / "access.log").read_text(encoding="utf-8") == label
        assert (root / "ready").read_text(encoding="utf-8") == label

    assert canonical_sentinel.read_text(encoding="utf-8") == "canonical"
    for label, root, _process in processes[1:]:
        assert root == identities[label].state_root
        assert {path.name for path in root.iterdir()} == {
            "access.log",
            "catalog.sqlite3",
            "ready",
        }


def test_canonical_migration_update_guard_and_selective_removal_preserve_neighbors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    canonical = home / ".odcli"
    canonical.mkdir(mode=0o700)
    (canonical / "sentinel").write_text("canonical", encoding="utf-8")
    legacy = {name: tmp_path / f"legacy-{name}" for name in ("config", "data", "cache", "state")}
    for root in legacy.values():
        root.mkdir()
    (legacy["data"] / "sentinel").write_text("legacy", encoding="utf-8")

    numbered_one = identity("numbered", "1", home=home)
    numbered_two = identity("numbered", "2", home=home)
    hotfix_42 = identity("hotfix", "42", home=home)
    hotfix_43 = identity("hotfix", "43", home=home)
    _publish_fixture(numbered_one, sha=_SHA_BY_LABEL["numbered-1"], column="one_payload")
    _publish_fixture(numbered_two, sha=_SHA_BY_LABEL["numbered-2"], column="two_payload")
    _publish_fixture(hotfix_42, sha=_SHA_BY_LABEL["hotfix-42"], column="forty_two_payload", pr=42)
    _publish_fixture(hotfix_43, sha=_SHA_BY_LABEL["hotfix-43"], column="forty_three_payload", pr=43)
    for root, label in (
        (numbered_one.state_root, "one"),
        (numbered_two.state_root, "two"),
        (hotfix_42.state_root, "forty-two"),
        (hotfix_43.state_root, "forty-three"),
    ):
        root.mkdir(mode=0o700)
        (root / "sentinel").write_text(label, encoding="utf-8")

    preserved = {
        numbered_two.state_root: _tree_digest(numbered_two.state_root),
        hotfix_43.state_root: _tree_digest(hotfix_43.state_root),
        numbered_two.manager_root: _tree_digest(numbered_two.manager_root),
        hotfix_43.manager_root: _tree_digest(hotfix_43.manager_root),
    }

    assert migrate_storage(home=home, legacy=legacy).state == "complete"
    assert (canonical / "sentinel").read_text(encoding="utf-8") == "canonical"
    assert (legacy["data"] / "sentinel").exists()
    for root, digest in preserved.items():
        assert _tree_digest(root) == digest

    monkeypatch.setenv("ODCLI_LAUNCHER_KIND", "numbered")
    monkeypatch.setenv("ODCLI_LAUNCHER_ID", "2")
    with pytest.raises(UpdateError, match="odcli slot install 2"):
        assert_direct_update_allowed()

    remove_alternate_tool(numbered_one)
    remove_alternate_tool(hotfix_42)
    for removed in (numbered_one, hotfix_42):
        assert not removed.launcher.exists()
        assert not removed.manifest.exists()
        assert not removed.tool_root.exists()
        assert not removed.tool_bin.exists()
        assert not removed.state_root.exists()
    for root, digest in preserved.items():
        assert _tree_digest(root) == digest
