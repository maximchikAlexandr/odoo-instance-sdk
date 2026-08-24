from __future__ import annotations

import os
import subprocess
import tarfile
import tomllib
import zipfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.packaging]

_REPO = Path(__file__).resolve().parents[2]


def _dist() -> tuple[Path, Path]:
    dist = _REPO / "dist"
    wheels = list(dist.glob("*.whl")) if dist.is_dir() else []
    sdists = list(dist.glob("*.tar.gz")) if dist.is_dir() else []
    if len(wheels) != 1 or len(sdists) != 1:
        pytest.fail("dist/ must contain exactly one wheel and one sdist; run `make package`")
    return wheels[0], sdists[0]


def test_wheel_contains_package_typed_marker_and_odcli() -> None:
    wheel, _sdist = _dist()
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
        entry = zf.read(next(n for n in names if n.endswith("entry_points.txt"))).decode()
    assert any(n.endswith("odoo_instance_sdk/__init__.py") for n in names)
    assert any(n.endswith("odoo_instance_sdk/py.typed") for n in names)
    assert any(n.endswith("odoo_instance_sdk/web/dist/index.html") for n in names)
    assert any("odoo_instance_sdk/web/dist/assets/" in n for n in names)
    assert "odcli = odoo_instance_sdk.cli:cli" in entry


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
    subprocess.run(["uv", "venv", str(venv)], check=True, cwd=tmp_path)
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    odcli = venv / ("Scripts/odcli.exe" if os.name == "nt" else "bin/odcli")
    subprocess.run(
        ["uv", "pip", "install", "--python", str(python), str(artifact)],
        check=True,
        cwd=tmp_path,
    )
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
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


def test_isolated_wheel_import_and_odcli_help(tmp_path: Path) -> None:
    wheel, _sdist = _dist()
    _install_and_smoke(wheel, tmp_path)


def test_isolated_sdist_import_and_odcli_help(tmp_path: Path) -> None:
    _wheel, sdist = _dist()
    _install_and_smoke(sdist, tmp_path)


def test_current_version_artifacts() -> None:
    version = tomllib.loads((_REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    wheel, sdist = _dist()
    assert version in wheel.name
    assert version in sdist.name


def test_dashboard_source_has_stable_monitor_affordances() -> None:
    """Keep the user-visible card/open controls in the built dashboard contract.

    This repository has no browser/component-test runner; source-level selectors
    are the narrow deterministic boundary and the package gate separately proves
    that Vite type-checks and bundles the same source.
    """
    app = (_REPO / "src/odoo_instance_sdk/web/src/App.tsx").read_text(encoding="utf-8")
    assert 'data-testid="cluster-card"' in app
    assert 'data-testid="environment-card"' in app
    assert 'data-testid="open-odoo"' in app
    assert "window.open(rt.http_url" in app
