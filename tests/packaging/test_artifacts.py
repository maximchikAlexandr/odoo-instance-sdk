from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from email import message_from_bytes
from pathlib import Path

import pytest

from odoo_instance_sdk.internal import alternate_tool
from odoo_instance_sdk.internal.gitlab_credentials import (
    cleanup_askpass_channel,
    write_askpass_channel,
)
from odoo_instance_sdk.internal.proc.redaction import redacted_projection
from odoo_instance_sdk.models import GitLabCredentialContext, GitLabCredentialIdentity

pytestmark = [pytest.mark.packaging, pytest.mark.timeout(180)]

_REPO = Path(__file__).resolve().parents[2]
_SDIST_SMOKE_TEST_TIMEOUT_SECONDS = 180


def _isolated_process_env(root: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    for name in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        path = root / name.lower()
        path.mkdir(parents=True, exist_ok=True)
        env[name] = str(path)
    return env


def _dist() -> tuple[Path, Path]:
    dist = _REPO / "dist"
    wheels = list(dist.glob("*.whl")) if dist.is_dir() else []
    sdists = list(dist.glob("*.tar.gz")) if dist.is_dir() else []
    if not dist.is_dir() or (not wheels and not sdists):
        pytest.skip("package artifacts are unavailable; run `make package` for the package gate")
    if len(wheels) != 1 or len(sdists) != 1:
        pytest.fail("dist/ must contain exactly one wheel and one sdist; run `make package`")
    return wheels[0], sdists[0]


def test_wheel_contains_package_typed_marker_and_odcli() -> None:
    wheel, _sdist = _dist()
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
        entry = zf.read(next(n for n in names if n.endswith("entry_points.txt"))).decode()
    assert any(n.endswith("odoo_instance_sdk/__init__.py") for n in names)
    assert any(n.endswith("odoo_instance_sdk/internal/alternate_tool.py") for n in names)
    assert any(n.endswith("odoo_instance_sdk/internal/agent_checkout.py") for n in names)
    assert any(n.endswith("odoo_instance_sdk/py.typed") for n in names)
    assert any(n.endswith("odoo_instance_sdk/web/dist/index.html") for n in names)
    assert any("odoo_instance_sdk/web/dist/assets/" in n for n in names)
    assert "odcli = odoo_instance_sdk.cli:cli" in entry
    assert "odcli-git-askpass = odoo_instance_sdk.internal.git_askpass:main" in entry


def test_sdist_contains_package() -> None:
    _wheel, sdist = _dist()
    with tarfile.open(sdist) as tf:
        names = tf.getnames()
    assert any("src/odoo_instance_sdk" in name for name in names)
    assert any(name.endswith("src/odoo_instance_sdk/web/dist/index.html") for name in names)
    assert any("src/odoo_instance_sdk/web/dist/assets/" in name for name in names)


def _install_and_smoke(artifact: Path, tmp_path: Path) -> None:
    venv = tmp_path / "venv"
    empty = tmp_path / "empty"
    empty.mkdir()
    subprocess.run(["uv", "venv", "--python", sys.executable, str(venv)], check=True, cwd=tmp_path)
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    odcli = venv / ("Scripts/odcli.exe" if os.name == "nt" else "bin/odcli")
    subprocess.run(
        ["uv", "pip", "install", "--python", str(python), str(artifact)],
        check=True,
        cwd=tmp_path,
    )
    env = _isolated_process_env(tmp_path / "user-state")
    imported = subprocess.run(
        [str(python), "-c", "import odoo_instance_sdk; print(odoo_instance_sdk.__file__)"],
        cwd=empty,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "odoo_instance_sdk" in imported.stdout
    assert str(_REPO) not in imported.stdout
    help_result = subprocess.run(
        [str(odcli), "--help"],
        cwd=empty,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Usage" in help_result.stdout
    short_help_result = subprocess.run(
        [str(odcli), "-h"],
        cwd=empty,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert short_help_result.stdout == help_result.stdout
    init_help_result = subprocess.run(
        [str(odcli), "init", "-h"],
        cwd=empty,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "--venv" in init_help_result.stdout
    assert "prepare-venv" in init_help_result.stdout
    startup_import = subprocess.run(
        [
            str(python),
            "-c",
            (
                "import sys; from odoo_instance_sdk.cli import cli; "
                "forbidden = ('odoo_instance_sdk.resources.monitor', "
                "'odoo_instance_sdk.internal.dbprep.source', "
                "'odoo_instance_sdk.internal.dbprep.source_restore', "
                "'alembic', 'sqlalchemy'); "
                "assert not set(forbidden).intersection(sys.modules)"
            ),
        ],
        cwd=empty,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert startup_import.returncode == 0, startup_import.stderr
    metadata = subprocess.run(
        [
            str(python),
            "-c",
            "from importlib.metadata import version; print(version('odoo-instance-sdk'))",
        ],
        cwd=empty,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    version_result = subprocess.run(
        [str(odcli), "--version"],
        cwd=empty,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert version_result.stdout.strip() == f"odcli, version {metadata}"
    assert version_result.stderr == ""
    askpass = venv / (
        "Scripts/odcli-git-askpass.exe" if os.name == "nt" else "bin/odcli-git-askpass"
    )
    channel = tmp_path / "askpass.json"
    channel.write_text(
        '[{"host":"gitlab.example","login":"test-user","token":"test-token"}]\n',
        encoding="utf-8",
    )
    askpass_result = subprocess.run(
        [str(askpass), "Password for 'https://gitlab.example/team/repo':"],
        cwd=empty,
        env=env | {"ODCLI_GITLAB_ASKPASS_FILE": str(channel)},
        check=False,
        capture_output=True,
        text=True,
    )
    assert askpass_result.returncode == 0
    assert askpass_result.stdout == "test-token\n"


def test_isolated_wheel_import_and_odcli_help(tmp_path: Path) -> None:
    wheel, _sdist = _dist()
    _install_and_smoke(wheel, tmp_path)


@pytest.mark.timeout(_SDIST_SMOKE_TEST_TIMEOUT_SECONDS)
def test_isolated_sdist_import_and_odcli_help(tmp_path: Path) -> None:
    """Allow the isolated sdist install and cold CLI import to finish."""
    _wheel, sdist = _dist()
    _install_and_smoke(sdist, tmp_path)


def test_numbered_install_publishes_executable_askpass_after_staging_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository = _REPO.resolve().as_uri()
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=_REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    monkeypatch.setattr(alternate_tool, "REPOSITORY_URL", repository)
    numbered = alternate_tool.identity("numbered", "1", home=tmp_path)
    manifest = alternate_tool.install_alternate_tool(numbered, sha, repository=repository)
    assert manifest.identity == numbered
    askpass = numbered.tool_bin / "odcli-git-askpass"
    assert askpass.is_file()
    assert not askpass.is_symlink()
    interpreter = alternate_tool._interpreter(askpass)
    assert interpreter.is_file()
    assert str(numbered.tool_root) in askpass.read_text(encoding="utf-8")
    assert not any(".1." in part for part in askpass.read_text(encoding="utf-8").splitlines())
    package_askpass = numbered.tool_root / "odoo-instance-sdk" / "bin" / "odcli-git-askpass"
    assert package_askpass.is_file()
    assert not package_askpass.is_symlink()
    assert str(numbered.tool_root) in package_askpass.read_text(encoding="utf-8")

    credential = GitLabCredentialContext(
        GitLabCredentialIdentity(
            user_id="11111111-1111-4111-8111-111111111111",
            host="gitlab.example",
            login="test-user",
            token_key="ODCLI_GITLAB_TOKEN_TEST",
        ),
        "credential-token",
    )
    channel = Path(write_askpass_channel(credential))
    try:
        result = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=gitlab.example\n\n",
            cwd=tmp_path,
            env=_isolated_process_env(tmp_path / "git-home")
            | dict(credential.askpass_environment(str(askpass), channel_path=str(channel)))
            | {"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1"},
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "username=test-user\n" in result.stdout
        assert "password=credential-token\n" in result.stdout
        safe_output = redacted_projection(
            result.stdout, secrets=("credential-token",), field="stdout"
        )
        assert isinstance(safe_output, str)
        assert "credential-token" not in safe_output
    finally:
        cleanup_askpass_channel(str(channel))
    assert not channel.exists()


def test_canonical_numbered_bootstrap_repairs_staged_askpass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Cover the pre-fix coordinator that only relocates the main launcher."""
    repository = _REPO.resolve().as_uri()
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=_REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    monkeypatch.setattr(alternate_tool, "REPOSITORY_URL", repository)
    numbered = alternate_tool.identity("numbered", "2", home=tmp_path)
    numbered.manager_root.parent.mkdir(mode=0o700, parents=True)
    stage = Path(tempfile.mkdtemp(prefix=".2.bootstrap-", dir=numbered.manager_root.parent))
    stage_tool = stage / "tool"
    stage_bin = stage / "bin"
    environment = _isolated_process_env(tmp_path / "bootstrap-home")
    environment.update({"UV_TOOL_DIR": str(stage_tool), "UV_TOOL_BIN_DIR": str(stage_bin)})
    try:
        subprocess.run(
            [
                "uv",
                "tool",
                "install",
                f"odoo-instance-sdk @ git+{repository}@{sha}",
            ],
            cwd=_REPO,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        staged_executable = stage_bin / "odcli"
        staged_interpreter = alternate_tool._interpreter(staged_executable)
        alternate_tool._materialize_staged_executable(staged_executable, stage_tool)
        relative_interpreter = staged_interpreter.absolute().relative_to(stage_tool.absolute())
        lines = staged_executable.read_text(encoding="utf-8").splitlines(keepends=True)
        lines[0] = f"#!{numbered.tool_root / relative_interpreter}\n"
        staged_executable.write_text("".join(lines), encoding="utf-8")
        numbered.manager_root.mkdir(mode=0o700, parents=True)
        os.replace(stage_tool, numbered.tool_root)
        os.replace(stage_bin, numbered.tool_bin)
    finally:
        if stage.exists():
            shutil.rmtree(stage)

    package_askpass = numbered.tool_root / "odoo-instance-sdk" / "bin" / "odcli-git-askpass"
    assert package_askpass.is_file()
    assert not package_askpass.is_symlink()
    assert ".2.bootstrap-" in package_askpass.read_text(encoding="utf-8")
    assert (numbered.tool_bin / "odcli-git-askpass").is_symlink()
    version = subprocess.run(
        [str(numbered.executable), "--version"],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert version.stdout.startswith("odcli, version ")
    assert package_askpass.is_file()
    assert not package_askpass.is_symlink()
    askpass_lines = package_askpass.read_text(encoding="utf-8").splitlines()
    assert askpass_lines and Path(askpass_lines[0][2:]).is_file()
    assert ".2.bootstrap-" not in "\n".join(askpass_lines[:2])

    credential = GitLabCredentialContext(
        GitLabCredentialIdentity(
            user_id="22222222-2222-4222-8222-222222222222",
            host="gitlab.example",
            login="bootstrap-user",
            token_key="ODCLI_GITLAB_TOKEN_BOOTSTRAP",
        ),
        "bootstrap-token",
    )
    channel = Path(write_askpass_channel(credential))
    try:
        result = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=gitlab.example\n\n",
            cwd=tmp_path,
            env=environment
            | dict(credential.askpass_environment(str(package_askpass), channel_path=str(channel)))
            | {"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1"},
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "username=bootstrap-user\n" in result.stdout
        assert "password=bootstrap-token\n" in result.stdout
        safe = redacted_projection(result.stdout, secrets=("bootstrap-token",), field="stdout")
        assert isinstance(safe, str)
        assert "bootstrap-token" not in safe
    finally:
        cleanup_askpass_channel(str(channel))
    assert not channel.exists()


def test_isolated_wheel_imports_rich_toon_and_runs_toon_env_list(tmp_path: Path) -> None:
    """The installed core package does not depend on Node or dashboard extras."""
    wheel, _sdist = _dist()
    venv = tmp_path / "venv"
    empty = tmp_path / "empty"
    empty.mkdir()
    subprocess.run(["uv", "venv", "--python", sys.executable, str(venv)], check=True, cwd=tmp_path)
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    odcli = venv / ("Scripts/odcli.exe" if os.name == "nt" else "bin/odcli")
    subprocess.run(
        ["uv", "pip", "install", "--python", str(python), str(wheel)],
        check=True,
        cwd=tmp_path,
    )
    env = _isolated_process_env(tmp_path / "user-state")
    imports = subprocess.run(
        [str(python), "-c", "import rich, toon; print(rich.__name__, toon.__name__)"],
        cwd=empty,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert imports.stdout.strip() == "rich toon"
    optional_imports = subprocess.run(
        [
            str(python),
            "-c",
            (
                "import importlib.util\n"
                "for module in ('fastapi', 'starlette', 'starlette_admin', 'pwdlib', 'sqlcipher3', 'uvicorn'):\n"
                "    assert importlib.util.find_spec(module) is None, module"
            ),
        ],
        cwd=empty,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert optional_imports.returncode == 0, optional_imports.stderr
    result = subprocess.run(
        [str(odcli), "env", "list", "--all-projects", "--format", "toon"],
        cwd=empty,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stderr == ""
    assert result.stdout.strip()
    assert "schema_version" in result.stdout
    assert str(Path.home()) not in result.stdout
    assert str(_REPO) not in result.stdout


def test_current_version_artifacts() -> None:
    version = tomllib.loads((_REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    wheel, sdist = _dist()
    assert version in wheel.name
    assert version in sdist.name


def _normalize_requirement(requirement: str) -> tuple[str, tuple[str, ...], str | None]:
    """Normalize wheel METADATA requirements without relying on build ordering.

    Core metadata has no marker; optional dashboard requirements carry the
    standard ``extra == 'dashboard'`` marker.  Sorting specifiers makes the
    contract stable across compliant build backends while retaining exact
    package names and bounds.
    """
    base, separator, marker = requirement.partition(";")
    match = re.fullmatch(r"\s*([A-Za-z0-9_.-]+)\s*(.*)\s*", base)
    assert match, f"unexpected Requires-Dist value: {requirement!r}"
    name, raw_specifiers = match.groups()
    specifiers = tuple(sorted(part.strip() for part in raw_specifiers.split(",") if part.strip()))
    normalized_marker = " ".join(marker.split()) if separator else None
    return name.lower().replace("_", "-"), specifiers, normalized_marker


def test_wheel_metadata_has_strict_core_and_dashboard_dependency_contract() -> None:
    """The built artifact, not only pyproject, defines the install contract."""
    wheel, _sdist = _dist()
    with zipfile.ZipFile(wheel) as zf:
        metadata_name = next(name for name in zf.namelist() if name.endswith(".dist-info/METADATA"))
        metadata = message_from_bytes(zf.read(metadata_name))

    requirements = [
        _normalize_requirement(value) for value in metadata.get_all("Requires-Dist", [])
    ]
    core = {(name, specifiers) for name, specifiers, marker in requirements if marker is None}
    dashboard = {
        (name, specifiers)
        for name, specifiers, marker in requirements
        if marker == "extra == 'dashboard'"
    }
    provides_extra = {value.lower() for value in metadata.get_all("Provides-Extra", [])}

    assert core == {
        ("alembic", ("<2", ">=1.13")),
        ("click", ("<9", ">=8.2")),
        ("expression", ("<6", ">=5")),
        ("httpx", ("<1.0", ">=0.27")),
        ("json5", ("<1", ">=0.15")),
        ("msgspec", ("<1.0", ">=0.18")),
        ("platformdirs", ("<5", ">=4.3")),
        ("psutil", ("<7", ">=5.9")),
        ("python-toon", ("==0.1.3",)),
        ("rich", ("<16", ">=15")),
        ("rich-click", ("<2", ">=1.9")),
        ("sqlalchemy", ("<3", ">=2")),
    }
    assert dashboard == {
        ("fastapi", ("<1.0", ">=0.141")),
        ("pwdlib", ("<1.0", "[argon2]>=0.3")),
        ("sqlcipher3", ("<1.0", ">=0.6")),
        ("starlette", ("<2.0", ">=1.3.1")),
        ("starlette-admin", ("<2.0", ">=1.0")),
        ("uvicorn", ("<1.0", ">=0.30")),
    }
    assert provides_extra == {"dashboard"}
    assert "metrics" not in provides_extra


def test_dashboard_source_has_stable_monitor_affordances() -> None:
    """Keep both runtime card entries and actions in the packaged source.

    Vitest covers card behavior; this packaging guard checks that the source
    bundled by Vite retains both owner entry points and shared runtime controls.
    """
    app = (_REPO / "src/odoo_instance_sdk/web/src/App.tsx").read_text(encoding="utf-8")
    assert 'testId="project-card"' in app
    assert 'testId="environment-card"' in app
    assert 'actionTestId="open-project-odoo"' in app
    assert 'actionTestId="open-odoo"' in app
    assert 'data-testid="runtime-control"' in app
    assert 'window.open(externalUrl, "_blank", "noopener,noreferrer")' in app
