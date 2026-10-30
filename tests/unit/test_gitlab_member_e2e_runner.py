from __future__ import annotations

import base64
import csv
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tomllib
from argparse import Namespace
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import _gitlab_member_cache as cache, e2e_gitlab_member_workflow as runner


def _clean_sdk_checkout(repository: Path, revision: str, destination: Path) -> Path:
    subprocess.run(
        ["git", "clone", "--shared", "--no-checkout", str(repository), str(destination)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(destination), "checkout", "--detach", revision],
        check=True,
        capture_output=True,
        text=True,
    )
    return destination


def _prepare_public_cache_runner(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    repository: Path,
    revision: str,
    helper_dir: Path,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    checkout_a = root / "task-a"
    checkout_b = root / "task-b"
    checkout_a.mkdir()
    checkout_b.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setenv("MULTICA_DAEMON_PORT", "41001")
    monkeypatch.setenv("MULTICA_TOKEN", "task-token-fixture")
    environment = {
        "TEST_MULTICA_PROFILE": "isolated-profile",
        "TEST_GIT_HELPER_DIR": str(helper_dir),
        "TEST_CORE_PROJECT": str(repository),
        "TEST_SDK_SHA": revision,
        "TEST_MULTICA_PROJECT": "project-fixture",
        "TEST_MULTICA_WORKSPACE": "workspace-fixture",
        "TEST_GITLAB_CACHE_MEMBER": "00000000-0000-4000-8000-000000000001",
        "TEST_GITLAB_CACHE_OWNER_MISMATCH_MEMBER": "00000000-0000-4000-8000-000000000002",
        "TEST_GITLAB_CACHE_INACTIVE_MEMBER": "00000000-0000-4000-8000-000000000003",
        "TEST_GITLAB_CACHE_BROADER_SCOPE_MEMBER": "00000000-0000-4000-8000-000000000004",
        "TEST_GITLAB_CACHE_GRANULAR_SCOPE_MEMBER": "00000000-0000-4000-8000-000000000005",
        "TEST_GITLAB_CACHE_INCOMPATIBLE_MEMBER": "00000000-0000-4000-8000-000000000006",
        "TEST_GITLAB_MEMBER": "00000000-0000-4000-8000-000000000007",
        "TEST_GITLAB_MEMBER_B": "00000000-0000-4000-8000-000000000008",
        "TEST_GITLAB_MEMBER_MISSING": "00000000-0000-4000-8000-000000000009",
        "TEST_MULTICA_MEMBER_INVALID": "00000000-0000-4000-8000-000000000010",
        "TEST_MULTICA_MEMBER_NONHUMAN": "00000000-0000-4000-8000-000000000011",
        "TEST_MULTICA_MEMBER_CROSS_WORKSPACE": "00000000-0000-4000-8000-000000000012",
        "TEST_MULTICA_MEMBER_STALE": "00000000-0000-4000-8000-000000000013",
        "TEST_GITLAB_CACHE_HOST": "gitlab.example",
        "TEST_MULTICA_ISSUE_A": "MYL-1",
        "TEST_MULTICA_RUN_A": "run-a",
        "TEST_MULTICA_ISSUE_B": "MYL-2",
        "TEST_MULTICA_RUN_B": "run-b",
        "TEST_MULTICA_CHECKOUT_A": str(checkout_a),
        "TEST_MULTICA_CHECKOUT_B": str(checkout_b),
        "TEST_REPOSITORY_URL": "https://gitlab.example/group/project",
    }
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(cache, "_validate_integrated_checkouts", lambda *_args: None)
    monkeypatch.setattr(cache, "_secret_corpus", lambda _member: ("fixture-secret",))
    monkeypatch.setattr(
        cache,
        "_integrated_cache_environments",
        lambda *_args: ({"PATH": str(helper_dir)}, {}),
    )


def _canary_installation(tmp_path: Path, repository: Path, canary: Path) -> Path:
    install_root = tmp_path / "candidate-venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(install_root)],
        check=True,
        capture_output=True,
        text=True,
    )
    helper_dir = install_root / "bin"
    interpreter = helper_dir / "python"
    site_packages = next(install_root.glob("lib/python*/site-packages"))
    package_root = site_packages / "odoo_instance_sdk"
    internal = package_root / "internal"
    internal.mkdir(parents=True)
    (package_root / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(canary)!r}).write_text('candidate package executed')\n",
        encoding="utf-8",
    )
    (internal / "__init__.py").write_text("", encoding="utf-8")
    (internal / "git_credential_helper.py").write_text(
        "def main():\n    return 1\n", encoding="utf-8"
    )
    dist_info = site_packages / "odoo_instance_sdk-0.1.0.dist-info"
    dist_info.mkdir()
    project = tomllib.loads((repository / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    (dist_info / "METADATA").write_text(
        f"Metadata-Version: 2.4\nName: {project['name']}\nVersion: {project['version']}\n",
        encoding="utf-8",
    )
    entry_points = "[console_scripts]\n" + "".join(
        f"{name} = {value}\n" for name, value in project["scripts"].items()
    )
    (dist_info / "entry_points.txt").write_text(entry_points, encoding="utf-8")
    (dist_info / "direct_url.json").write_text(
        json.dumps({"url": repository.as_uri(), "dir_info": {"editable": False}}),
        encoding="utf-8",
    )
    helper = helper_dir / "git-credential-odcli"
    helper.write_bytes(
        (
            f"#!{interpreter}\n"
            "# -*- coding: utf-8 -*-\n"
            "import sys\n"
            "from odoo_instance_sdk.internal.git_credential_helper import main\n"
            'if __name__ == "__main__":\n'
            '    if sys.argv[0].endswith("-script.pyw"):\n'
            "        sys.argv[0] = sys.argv[0][:-11]\n"
            '    elif sys.argv[0].endswith(".exe"):\n'
            "        sys.argv[0] = sys.argv[0][:-4]\n"
            "    sys.exit(main())\n"
        ).encode()
    )
    helper.chmod(0o755)
    rows: list[tuple[str, str, str]] = []
    files = (*package_root.rglob("*"), *dist_info.iterdir())
    for path in files:
        if path.is_file() and path.name != "RECORD":
            content = path.read_bytes()
            digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).decode().rstrip("=")
            rows.append(
                (str(path.relative_to(site_packages)), f"sha256={digest}", str(len(content)))
            )
    content = helper.read_bytes()
    digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).decode().rstrip("=")
    rows.extend(
        (
            ("../../../bin/git-credential-odcli", f"sha256={digest}", str(len(content))),
            (f"{dist_info.name}/RECORD", "", ""),
        )
    )
    with (dist_info / "RECORD").open("w", newline="", encoding="utf-8") as stream:
        csv.writer(stream).writerows(rows)
    return helper_dir


@pytest.mark.unit
def test_public_cache_runner_rejects_untrusted_package_before_probe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repository = Path(__file__).parents[2].resolve()
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository, capture_output=True, text=True, check=True
    ).stdout.strip()
    clean_repository = _clean_sdk_checkout(repository, revision, tmp_path / "clean-sdk")
    canary = tmp_path / "candidate-package-imported"
    helper_dir = _canary_installation(tmp_path, clean_repository, canary)
    _prepare_public_cache_runner(
        monkeypatch, tmp_path / "runner", clean_repository, revision, helper_dir
    )
    daemon_status = Mock(side_effect=AssertionError("daemon was queried before provenance passed"))
    daemon_stop = Mock(side_effect=AssertionError("daemon was mutated before provenance passed"))
    monkeypatch.setattr(cache, "_profile_status", daemon_status)
    monkeypatch.setattr(cache, "_profile_stop", daemon_stop)

    result = runner.main(
        ["--mode", "multica-cache", "--work-root", str(tmp_path / "runner" / "runner-root")]
    )
    response = json.loads(capsys.readouterr().out)

    assert result == 1
    assert response["ok"] is False
    assert not canary.exists()
    assert "candidate SDK package bytes do not match" in response["error"]
    daemon_status.assert_not_called()
    daemon_stop.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize(
    "mutate_package",
    [
        pytest.param(
            lambda package_root: (
                package_root / "internal" / "git_credential_helper.py"
            ).write_text(
                (package_root / "internal" / "git_credential_helper.py").read_text(encoding="utf-8")
                + "\n# dirty candidate\n",
                encoding="utf-8",
            ),
            id="tracked-dirty",
        ),
        pytest.param(
            lambda package_root: (package_root / "untracked_canary.py").write_text(
                "from pathlib import Path\nPath('candidate-package-imported').write_text('executed')\n",
                encoding="utf-8",
            ),
            id="untracked-python",
        ),
    ],
)
def test_public_cache_runner_rejects_dirty_candidate_repository_before_probe_or_daemon(
    mutate_package: Callable[[Path], object],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = Path(__file__).parents[2].resolve()
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository, capture_output=True, text=True, check=True
    ).stdout.strip()
    dirty_repository = _clean_sdk_checkout(repository, revision, tmp_path / "dirty-sdk")
    package_root = dirty_repository / "src" / "odoo_instance_sdk"
    mutate_package(package_root)
    canary = tmp_path / "candidate-package-imported"
    helper_dir = _canary_installation(tmp_path, dirty_repository, canary)
    _prepare_public_cache_runner(
        monkeypatch, tmp_path / "runner", dirty_repository, revision, helper_dir
    )
    daemon_status = Mock(
        return_value={"profile": "isolated-profile", "status": "running", "pid": 72}
    )
    daemon_stop = Mock()
    lifecycle_gate = Mock(side_effect=RuntimeError("candidate lifecycle was reached"))
    monkeypatch.setattr(cache, "_profile_status", daemon_status)
    monkeypatch.setattr(cache, "_profile_stop", daemon_stop)
    monkeypatch.setattr(cache, "_stop_and_validate_cache_generation", lifecycle_gate)

    result = runner.main(
        ["--mode", "multica-cache", "--work-root", str(tmp_path / "runner" / "runner-root")]
    )
    response = json.loads(capsys.readouterr().out)

    assert result == 1
    assert response["ok"] is False
    assert not canary.exists()
    assert "candidate SDK repository must be clean" in response["error"]
    daemon_status.assert_not_called()
    daemon_stop.assert_not_called()
    lifecycle_gate.assert_not_called()


def _exact_candidate_installation(tmp_path: Path, repository: Path) -> Path:
    install_root = tmp_path / "exact-candidate-venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(install_root)],
        check=True,
        capture_output=True,
        text=True,
    )
    helper_dir = install_root / "bin"
    interpreter = helper_dir / "python"
    site_packages = next(install_root.glob("lib/python*/site-packages"))
    dist_info = site_packages / "odoo_instance_sdk-0.1.0.dist-info"
    dist_info.mkdir()
    project = tomllib.loads((repository / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    (dist_info / "METADATA").write_text(
        f"Metadata-Version: 2.4\nName: {project['name']}\nVersion: {project['version']}\n",
        encoding="utf-8",
    )
    (dist_info / "entry_points.txt").write_text(
        "[console_scripts]\n"
        + "".join(f"{name} = {value}\n" for name, value in project["scripts"].items()),
        encoding="utf-8",
    )
    (dist_info / "direct_url.json").write_text(
        json.dumps({"url": repository.as_uri(), "dir_info": {"editable": True}}),
        encoding="utf-8",
    )
    (site_packages / "odoo_instance_sdk.pth").write_text(
        str(repository / "src") + "\n", encoding="utf-8"
    )
    helper = helper_dir / "git-credential-odcli"
    helper.write_bytes(
        (
            f"#!{interpreter}\n"
            "# -*- coding: utf-8 -*-\n"
            "import sys\n"
            "from odoo_instance_sdk.internal.git_credential_helper import main\n"
            'if __name__ == "__main__":\n'
            '    if sys.argv[0].endswith("-script.pyw"):\n'
            "        sys.argv[0] = sys.argv[0][:-11]\n"
            '    elif sys.argv[0].endswith(".exe"):\n'
            "        sys.argv[0] = sys.argv[0][:-4]\n"
            "    sys.exit(main())\n"
        ).encode()
    )
    helper.chmod(0o755)
    files = (*site_packages.glob("*.pth"), *dist_info.iterdir())
    rows: list[tuple[str, str, str]] = []
    for path in files:
        if path.is_file() and path.name != "RECORD":
            content = path.read_bytes()
            digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).decode().rstrip("=")
            rows.append(
                (str(path.relative_to(site_packages)), f"sha256={digest}", str(len(content)))
            )
    content = helper.read_bytes()
    digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).decode().rstrip("=")
    rows.extend(
        (
            ("../../../bin/git-credential-odcli", f"sha256={digest}", str(len(content))),
            (f"{dist_info.name}/RECORD", "", ""),
        )
    )
    with (dist_info / "RECORD").open("w", newline="", encoding="utf-8") as stream:
        csv.writer(stream).writerows(rows)
    return helper_dir


def _public_security_fixture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    secret_corpus: Mock | None = None,
) -> tuple[Path, Path, str, Mock]:
    repository = Path(__file__).parents[2].resolve()
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository, capture_output=True, text=True, check=True
    ).stdout.strip()
    clean_repository = _clean_sdk_checkout(repository, revision, tmp_path / "clean-sdk")
    helper_dir = _exact_candidate_installation(tmp_path, clean_repository)
    runner_root = tmp_path / "runner"
    _prepare_public_cache_runner(monkeypatch, runner_root, clean_repository, revision, helper_dir)
    token_key = "ODCLI_GITLAB_TOKEN_CACHE"
    token_value = "mapped-token-fixture"
    monkeypatch.setattr(
        cache,
        "read_gitlab_credential_mappings",
        lambda: (
            SimpleNamespace(
                user_id="00000000-0000-4000-8000-000000000001",
                host="gitlab.example",
                login="alice",
                token_key=token_key,
            ),
        ),
    )
    monkeypatch.setenv(token_key, token_value)
    environment = {
        "PATH": str(helper_dir),
        token_key: token_value,
    }
    monkeypatch.setattr(cache, "_integrated_cache_environments", lambda *_args: (environment, {}))
    monkeypatch.setattr(
        cache,
        "_profile_status",
        lambda _args: {"profile": "isolated-profile", "status": "running", "pid": 72},
    )
    daemon_stop = Mock()
    monkeypatch.setattr(cache, "_profile_stop", daemon_stop)
    if secret_corpus is not None:
        monkeypatch.setattr(cache, "_secret_corpus", secret_corpus)
    return helper_dir, runner_root, token_value, daemon_stop


def _trusted_git_fixture(tmp_path: Path) -> Path:
    real_git = shutil.which("git")
    assert real_git is not None
    executable = tmp_path / "trusted-bin" / "git"
    trace = tmp_path / "trusted-git-credential-called"
    executable.parent.mkdir()
    executable.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "credential" ] && [ "$2" = "fill" ]; then\n'
        f"  printf '%s\\n' \"$GIT_CONFIG_VALUE_1:$PATH\" > {shlex.quote(str(trace))}\n"
        '  helper="$(command -v git-credential-odcli)"\n'
        '  exec "$helper" get\n'
        "fi\n"
        f'exec {shlex.quote(real_git)} "$@"\n',
        encoding="utf-8",
    )
    executable.chmod(0o700)
    return executable


def _compile_candidate_interpreter_canary(executable: Path, sentinel: Path) -> None:
    compiler = shutil.which("cc")
    assert compiler is not None
    source = (
        "#include <stdio.h>\n"
        "#include <stdlib.h>\n"
        "int main(void) {\n"
        f'  FILE *file = fopen({json.dumps(str(sentinel))}, "w");\n'
        '  const char *token = getenv("ODCLI_GITLAB_TOKEN_CACHE");\n'
        "  if (file != NULL) { if (token != NULL) fputs(token, file); fclose(file); }\n"
        "  return 1;\n"
        "}\n"
    )
    subprocess.run(
        [compiler, "-x", "c", "-o", str(executable), "-"],
        input=source,
        capture_output=True,
        check=True,
        text=True,
    )


@pytest.mark.unit
def test_public_cache_runner_does_not_execute_candidate_git_with_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    helper_dir, runner_root, token, _daemon_stop = _public_security_fixture(monkeypatch, tmp_path)
    sentinel = tmp_path / "candidate-git-executed"
    candidate_git = helper_dir / "git"
    candidate_git.write_text(
        "#!/bin/sh\nprintf '%s' \"$ODCLI_GITLAB_TOKEN_CACHE\" > "
        + repr(str(sentinel))
        + "\nexit 1\n",
        encoding="utf-8",
    )
    candidate_git.chmod(0o700)

    result = runner.main(
        ["--mode", "multica-cache", "--work-root", str(runner_root / "runner-root")]
    )
    response = json.loads(capsys.readouterr().out)

    assert result == 1
    assert response["ok"] is False
    assert not sentinel.exists()
    assert token not in json.dumps(response)


@pytest.mark.unit
def test_public_cache_runner_rejects_untrusted_interpreter_before_secret_or_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret_corpus = Mock(return_value=("mapped-token-fixture",))
    helper_dir, runner_root, token, daemon_stop = _public_security_fixture(
        monkeypatch, tmp_path, secret_corpus=secret_corpus
    )
    sentinel = tmp_path / "candidate-interpreter-executed"
    interpreter = helper_dir / "python"
    interpreter.unlink()
    _compile_candidate_interpreter_canary(interpreter, sentinel)
    trusted_git = _trusted_git_fixture(tmp_path)
    result = runner.main(
        [
            "--mode",
            "multica-cache",
            "--git",
            str(trusted_git),
            "--work-root",
            str(runner_root / "runner-root"),
        ]
    )
    response = json.loads(capsys.readouterr().out)

    assert result == 1
    assert response["ok"] is False
    assert not sentinel.exists()
    assert token not in json.dumps(response)
    assert not (tmp_path / "trusted-git-credential-called").exists()
    secret_corpus.assert_not_called()
    daemon_stop.assert_not_called()


@pytest.mark.unit
def test_installed_helper_provenance_accepts_exact_candidate_installation(
    tmp_path: Path,
) -> None:
    repository = Path(__file__).parents[2].resolve()
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository, capture_output=True, text=True, check=True
    ).stdout.strip()
    clean_repository = _clean_sdk_checkout(repository, revision, tmp_path / "clean-sdk")
    helper_dir = _exact_candidate_installation(tmp_path, clean_repository)

    helper = cache._installed_helper_provenance(
        Namespace(
            git="git", sdk_sha=revision, helper_dir=helper_dir, core_project=clean_repository
        ),
        {"PATH": str(helper_dir)},
    )

    assert helper == (helper_dir / "git-credential-odcli").resolve()
    assert helper.is_relative_to(helper_dir.resolve())


@pytest.mark.unit
def test_installed_helper_provenance_rejects_escaped_launcher_symlink(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    helper_dir = tmp_path / "owned" / "bin"
    helper_dir.mkdir(parents=True)
    foreign_helper = tmp_path / "foreign-helper"
    foreign_helper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    foreign_helper.chmod(0o700)
    (helper_dir / "git-credential-odcli").symlink_to(foreign_helper)
    monkeypatch.setattr(
        shutil, "which", lambda *_args, **_kwargs: str(helper_dir / "git-credential-odcli")
    )

    with pytest.raises(RuntimeError, match="escapes its owned installation"):
        cache._installed_helper_provenance(
            Namespace(helper_dir=helper_dir, core_project=Path(__file__).parents[2]), {}
        )


@pytest.mark.unit
def test_installed_helper_provenance_rejects_installation_bound_to_another_project(
    tmp_path: Path,
) -> None:
    import sys

    repository = Path(__file__).parents[2].resolve()
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository, capture_output=True, text=True, check=True
    ).stdout.strip()
    clean_repository = _clean_sdk_checkout(repository, revision, tmp_path / "clean-sdk")
    helper_dir = Path(sys.executable).parent

    with pytest.raises(RuntimeError, match="points to a different project"):
        cache._installed_helper_provenance(
            Namespace(
                git="git", sdk_sha=revision, helper_dir=helper_dir, core_project=clean_repository
            ),
            {"PATH": str(helper_dir)},
        )


@pytest.mark.unit
def test_public_cache_runner_rejects_global_helper_before_daemon_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MULTICA_DAEMON_PORT", "41001")
    monkeypatch.setenv("MULTICA_TOKEN", "task-token-fixture")
    monkeypatch.setenv("TEST_GIT_HELPER_DIR", str(tmp_path / "owned" / "bin"))
    monkeypatch.setenv("TEST_CORE_PROJECT", str(Path(__file__).parents[2].resolve()))
    monkeypatch.setenv("PATH", str(tmp_path / "global-bin"))
    owned_bin = tmp_path / "owned" / "bin"
    owned_bin.mkdir(parents=True)
    global_bin = tmp_path / "global-bin"
    global_bin.mkdir()
    global_helper = global_bin / "git-credential-odcli"
    global_helper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    global_helper.chmod(0o700)
    monkeypatch.setattr(
        cache,
        "_validate_integrated_cache_inputs",
        lambda _args: ("member", "a", "b", (tmp_path, tmp_path / "other")),
    )
    monkeypatch.setattr(cache, "_validate_integrated_checkouts", lambda *_args: None)
    monkeypatch.setattr(cache, "_secret_corpus", lambda _member: ("secret-fixture",))
    monkeypatch.setattr(
        cache, "_integrated_cache_environments", lambda *_args: ({"PATH": str(global_bin)}, {})
    )
    lifecycle = Mock(side_effect=AssertionError("daemon lifecycle mutation occurred"))
    monkeypatch.setattr(cache, "_profile_status", lifecycle)
    monkeypatch.setattr(cache, "_profile_stop", lifecycle)

    result = runner.main(["--mode", "multica-cache"])
    response = json.loads(capsys.readouterr().out)

    assert result == 1
    assert response["ok"] is False
    assert response["error"] == "candidate SDK credential helper is unavailable"
    lifecycle.assert_not_called()


@pytest.mark.unit
def test_cache_generation_stops_validates_starts_and_checks_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = Namespace(multica="multica", profile="isolated")
    events: list[str] = []

    responses = iter(
        (
            ("start", SimpleNamespace(stdout="")),
            (
                "status",
                SimpleNamespace(
                    stdout=json.dumps({"profile": "isolated", "status": "running", "pid": 42})
                ),
            ),
        )
    )

    def execute(*_args: object, **_kwargs: object) -> SimpleNamespace:
        event, response = next(responses)
        events.append(event)
        return response

    run_mock = Mock(side_effect=execute)
    monkeypatch.setattr(cache, "_run", run_mock)
    monkeypatch.setattr(cache, "_profile_stop", lambda _args: events.append("stop"))
    monkeypatch.setattr(
        cache,
        "_validate_cache_helper",
        lambda *_args, **_kwargs: events.append("validate-installed-helper"),
    )

    cache._stop_and_validate_cache_generation(
        args,
        {
            "MULTICA_DAEMON_PORT": "41001",
            "MULTICA_TOKEN": "task-token-fixture",
            "ODCLI_GITLAB_PURPOSE": "cache-read",
        },
        "member",
        ("secret-fixture",),
        stop_running_generation=True,
    )
    status = cache._profile_start(
        args,
        {
            "MULTICA_DAEMON_PORT": "41001",
            "MULTICA_TOKEN": "task-token-fixture",
            "ODCLI_GITLAB_PURPOSE": "cache-read",
        },
    )

    assert events == ["stop", "validate-installed-helper", "start", "status"]
    invocations = run_mock.call_args_list
    assert [tuple(invocation.args[0]) for invocation in invocations] == [
        ("multica", "--profile", "isolated", "daemon", "start"),
        ("multica", "--profile", "isolated", "daemon", "status", "--output", "json"),
    ]
    start_environment = invocations[0].kwargs["env"]
    assert "MULTICA_TOKEN" not in start_environment
    assert "MULTICA_DAEMON_PORT" not in start_environment
    assert status["status"] == "running"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("returncode", "stdout"),
    [(1, ""), (0, "username=mallory\npassword=secret-fixture\n")],
    ids=["helper-refusal", "wrong-login"],
)
def test_installed_helper_validation_rejects_refusal_and_wrong_login(
    monkeypatch: pytest.MonkeyPatch, returncode: int, stdout: str
) -> None:
    member_id = "00000000-0000-4000-8000-000000000014"
    args = Namespace(
        git="git",
        cache_host="gitlab.example",
        helper_dir=Path("/owned/bin"),
        cache_member_id=member_id,
    )
    captured: list[tuple[list[str], str, dict[str, str]]] = []
    monkeypatch.setattr(
        cache,
        "read_gitlab_credential_mappings",
        lambda: (
            SimpleNamespace(
                user_id=member_id,
                host="gitlab.example",
                login="alice",
                token_key="ODCLI_GITLAB_TOKEN_CACHE",
            ),
        ),
    )

    def execute(
        argv: list[str], *, input: str, env: dict[str, str], **_kwargs: object
    ) -> SimpleNamespace:
        captured.append((argv, input, env))
        return SimpleNamespace(returncode=returncode, stdout=stdout)

    monkeypatch.setattr(
        cache, "_installed_helper_provenance", lambda *_args: Path("/owned/bin/helper")
    )
    monkeypatch.setattr(subprocess, "run", execute)
    with pytest.raises(RuntimeError, match="rejected"):
        cache._validate_cache_helper(
            args,
            {"ODCLI_GITLAB_PURPOSE": "cache-read"},
            member_id,
            ("secret-fixture",),
        )

    assert len(captured) == 1
    argv, request, _environment = captured[0]
    trusted_git = shutil.which("git")
    assert trusted_git is not None
    assert argv == [str(Path(trusted_git).resolve()), "credential", "fill"]
    assert request == "protocol=https\nhost=gitlab.example\n\n"
    assert "secret-fixture" not in request


@pytest.mark.unit
def test_installed_helper_validation_accepts_exact_git_fill_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    member_id = "00000000-0000-4000-8000-000000000015"
    args = Namespace(
        git="git",
        cache_host="gitlab.example",
        helper_dir=Path("/owned/bin"),
        cache_member_id=member_id,
    )
    environment = {
        "ODCLI_GITLAB_PURPOSE": "cache-read",
        "ODCLI_GITLAB_SELECTED_HOST": "gitlab.example",
        "ODCLI_GITLAB_TOKEN_CACHE": "secret-fixture",
        "MULTICA_TOKEN": "must-not-reach-helper",
        "PYTHONPATH": "/untrusted/site",
    }
    result = SimpleNamespace(returncode=0, stdout="username=alice\npassword=secret-fixture\n")
    run_mock = Mock(return_value=result)
    monkeypatch.setattr(
        cache,
        "_installed_helper_provenance",
        lambda *_args: Path("/owned/bin/git-credential-odcli"),
    )
    monkeypatch.setattr(
        cache,
        "read_gitlab_credential_mappings",
        lambda: (
            SimpleNamespace(
                user_id=member_id,
                host="gitlab.example",
                login="alice",
                token_key="ODCLI_GITLAB_TOKEN_CACHE",
            ),
        ),
    )
    monkeypatch.setattr(subprocess, "run", run_mock)

    cache._validate_cache_helper(args, environment, member_id, ("secret-fixture",))

    trusted_git = shutil.which("git")
    assert trusted_git is not None
    assert run_mock.call_args.args[0] == [
        str(Path(trusted_git).resolve()),
        "credential",
        "fill",
    ]
    assert run_mock.call_args.kwargs["input"] == "protocol=https\nhost=gitlab.example\n\n"
    helper_environment = run_mock.call_args.kwargs["env"]
    assert helper_environment["ODCLI_GITLAB_TOKEN_CACHE"] == "secret-fixture"
    assert helper_environment["PATH"].startswith("/owned/bin:")
    assert helper_environment["PYTHONNOUSERSITE"] == "1"
    assert helper_environment["PYTHONPYCACHEPREFIX"]
    assert helper_environment["GIT_CONFIG_GLOBAL"] == os.devnull
    assert helper_environment["GIT_CONFIG_VALUE_1"] == "!/owned/bin/git-credential-odcli"
    assert "MULTICA_TOKEN" not in helper_environment
    assert "PYTHONPATH" not in helper_environment
    assert run_mock.call_args.kwargs["capture_output"] is True


@pytest.mark.unit
def test_member_sync_command_keeps_complete_public_argv() -> None:
    args = Namespace(
        odcli_multica="odcli-multica",
        core_project=Path("/core"),
        multica_project="project",
        profile="isolated",
        workspace_id="workspace",
        repository_a="https://gitlab.example/team/repo",
    )

    command = runner._multica_sync_command(
        args,
        checkout=Path("/checkout"),
        issue="MYL-1",
        run_id="run-1",
        member_id="not-a-valid-member-id",
        dry_run=True,
    )

    assert command == [
        "odcli-multica",
        "git",
        "sync",
        "--project",
        "/core",
        "--multica-project",
        "project",
        "--issue",
        "MYL-1",
        "--run",
        "run-1",
        "--repository-url",
        "https://gitlab.example/team/repo",
        "--profile",
        "isolated",
        "--workspace-id",
        "workspace",
        "--member-id",
        "not-a-valid-member-id",
        "/checkout",
        "--format",
        "json",
        "--dry-run",
    ]


@pytest.mark.unit
def test_multica_cache_parser_accepts_exact_sha_and_negative_fixture_inputs() -> None:
    args = runner._parser().parse_args(
        [
            "--mode",
            "multica-cache",
            "--sdk-sha",
            "a" * 40,
            "--member-invalid",
            "invalid-member-fixture",
            "--cache-broader-scope-member",
            "broader-scope-fixture",
        ]
    )

    assert args.mode == "multica-cache"
    assert args.sdk_sha == "a" * 40
    assert args.member_invalid == "invalid-member-fixture"
    assert args.cache_broader_scope_member == "broader-scope-fixture"


@pytest.mark.unit
@pytest.mark.parametrize("status", ["stopped", "error"], ids=["stopped", "error"])
def test_profile_start_rejects_a_stopped_or_error_status(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    args = Namespace(multica="multica", profile="isolated")
    run_mock = Mock(
        side_effect=[
            SimpleNamespace(stdout=""),
            SimpleNamespace(stdout=json.dumps({"profile": "isolated", "status": status})),
        ]
    )
    monkeypatch.setattr(cache, "_run", run_mock)
    with pytest.raises(RuntimeError, match="did not become ready"):
        cache._profile_start(args, {})

    assert [tuple(call.args[0]) for call in run_mock.call_args_list] == [
        ("multica", "--profile", "isolated", "daemon", "start"),
        ("multica", "--profile", "isolated", "daemon", "status", "--output", "json"),
    ]


def _public_checkout_response(actual_checkout: Path, repository: str) -> SimpleNamespace:
    actual_checkout.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", str(actual_checkout)], check=True)
    subprocess.run(
        ["git", "-C", str(actual_checkout), "remote", "add", "origin", repository],
        check=True,
    )
    return SimpleNamespace(stdout=f"{actual_checkout}\n", stderr="checked out\n")


@pytest.mark.unit
def test_native_checkout_uses_the_public_daemon_repo_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MULTICA_DAEMON_PORT", "41001")
    monkeypatch.setenv("MULTICA_TOKEN", "task-token-fixture")
    monkeypatch.setenv("MULTICA_TASK_ID", "task-fixture")
    monkeypatch.setenv("MULTICA_WORKSPACE_ID", "workspace-fixture")
    monkeypatch.chdir(tmp_path)
    args = Namespace(
        multica="multica",
        git="git",
        profile="isolated",
        repository_a="https://gitlab.example/team/repo",
    )
    wrapper = tmp_path / "run" / "native-cache-checkout"
    actual_checkout = wrapper / "odoo-instance-sdk"
    daemon_status = {"profile": "isolated", "status": "running", "pid": 42}
    cache_environment = {
        "MULTICA_TASK_ID": "task-fixture",
        "MULTICA_WORKSPACE_ID": "workspace-fixture",
        "ODCLI_GITLAB_PURPOSE": "cache-read",
        "ODCLI_GITLAB_MEMBER_ID": "member-fixture",
        "ODCLI_GITLAB_SELECTED_HOST": "gitlab.example",
        "GIT_CONFIG_VALUE_1": "odcli",
    }
    daemon_environment = {
        **cache_environment,
        "MULTICA_DAEMON_PORT": "41001",
    }
    profile_status_mock = Mock(side_effect=[daemon_status, daemon_status])
    calls: list[tuple[tuple[str, ...], Path | None, dict[str, str] | None]] = []
    response_steps = iter(
        (
            lambda: SimpleNamespace(stdout=json.dumps(daemon_status)),
            lambda: _public_checkout_response(actual_checkout, args.repository_a),
            lambda: SimpleNamespace(stdout=json.dumps(daemon_status)),
        )
    )

    def execute(
        argv: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        **_kwargs: object,
    ) -> SimpleNamespace:
        calls.append((tuple(argv), cwd, env))
        return next(response_steps)()

    run_mock = Mock(side_effect=execute)
    monkeypatch.setattr(cache, "_run", run_mock)
    monkeypatch.setattr(cache, "_profile_status", profile_status_mock)
    monkeypatch.setattr(cache, "_daemon_environment", lambda _status: daemon_environment)
    cache._native_cache_checkout(
        args,
        tmp_path / "run",
        cache_environment,
        ("secret-fixture",),
        daemon_status,
    )
    recorded_calls: list[tuple[list[str], Path | None, dict[str, str] | None]] = [
        (list(call.args[0]), call.kwargs.get("cwd"), call.kwargs.get("env"))
        for call in run_mock.call_args_list
    ]

    checkout_calls = [call for call in recorded_calls if call[0][1:3] != ["daemon", "status"]]
    assert len(checkout_calls) == 1
    argv, cwd, environment = checkout_calls[0]
    assert argv == ["multica", "repo", "checkout", args.repository_a]
    assert cwd == wrapper
    assert environment is not None
    assert environment["MULTICA_DAEMON_PORT"] == "41001"
    assert environment["MULTICA_TOKEN"] == "task-token-fixture"
    assert environment["MULTICA_TASK_ID"] == "task-fixture"
    assert environment["MULTICA_WORKSPACE_ID"] == "workspace-fixture"
    status_calls = [call for call in recorded_calls if call[0][1:3] == ["daemon", "status"]]
    assert len(status_calls) == 2
    assert all(
        call[0] == ["multica", "daemon", "status", "--output", "json"] for call in status_calls
    )
    assert all(call[2] is not None for call in status_calls)
    assert all(call[2]["MULTICA_DAEMON_PORT"] == "41001" for call in status_calls if call[2])
    assert all(call[2]["MULTICA_TOKEN"] == "task-token-fixture" for call in status_calls if call[2])
    assert profile_status_mock.call_count == 2


@pytest.mark.unit
@pytest.mark.parametrize(
    ("stdout", "message"),
    [
        ("checked out", "one absolute worktree path"),
        ("/outside/checkout", "outside the owned checkout root"),
    ],
)
def test_native_checkout_rejects_invalid_public_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stdout: str, message: str
) -> None:
    monkeypatch.setenv("MULTICA_DAEMON_PORT", "41001")
    monkeypatch.setenv("MULTICA_TOKEN", "task-token-fixture")
    monkeypatch.setenv("MULTICA_TASK_ID", "task-fixture")
    monkeypatch.setenv("MULTICA_WORKSPACE_ID", "workspace-fixture")
    monkeypatch.chdir(tmp_path)
    args = Namespace(
        multica="multica",
        git="git",
        profile="isolated",
        repository_a="https://gitlab.example/team/repo",
    )
    status = {"profile": "isolated", "status": "running", "pid": 42}
    environment = {
        "MULTICA_TASK_ID": "task-fixture",
        "MULTICA_WORKSPACE_ID": "workspace-fixture",
        "ODCLI_GITLAB_PURPOSE": "cache-read",
        "ODCLI_GITLAB_MEMBER_ID": "member-fixture",
        "ODCLI_GITLAB_SELECTED_HOST": "gitlab.example",
        "GIT_CONFIG_VALUE_1": "odcli",
    }
    monkeypatch.setattr(cache, "_profile_status", lambda _args: status)
    monkeypatch.setattr(cache, "_daemon_environment", lambda _status: environment)
    run_mock = Mock(
        side_effect=[
            SimpleNamespace(stdout=json.dumps(status)),
            SimpleNamespace(stdout=stdout),
        ]
    )
    monkeypatch.setattr(cache, "_run", run_mock)

    with pytest.raises(RuntimeError, match=message):
        cache._native_cache_checkout(args, tmp_path / "run", environment, (), status)

    calls = run_mock.call_args_list
    assert len(calls) == 2
    assert calls[1].args[0] == ["multica", "repo", "checkout", args.repository_a]
    assert calls[1].kwargs["cwd"] == tmp_path / "run" / "native-cache-checkout"


@pytest.mark.unit
def test_native_checkout_fails_closed_on_invalid_task_status_response(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MULTICA_DAEMON_PORT", "41001")
    monkeypatch.setenv("MULTICA_TOKEN", "task-token-fixture")
    monkeypatch.setenv("MULTICA_TASK_ID", "task-fixture")
    monkeypatch.setenv("MULTICA_WORKSPACE_ID", "workspace-fixture")
    monkeypatch.chdir(tmp_path)
    args = Namespace(
        multica="multica",
        git="git",
        profile="isolated",
        repository_a="https://gitlab.example/team/repo",
    )
    status = {"profile": "isolated", "status": "running", "pid": 42}
    environment = {
        "MULTICA_TASK_ID": "task-fixture",
        "MULTICA_WORKSPACE_ID": "workspace-fixture",
        "ODCLI_GITLAB_PURPOSE": "cache-read",
        "ODCLI_GITLAB_MEMBER_ID": "member-fixture",
        "ODCLI_GITLAB_SELECTED_HOST": "gitlab.example",
        "GIT_CONFIG_VALUE_1": "odcli",
    }
    commands: list[list[str]] = []

    def execute(argv: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(argv)
        return SimpleNamespace(stdout="not-json")

    monkeypatch.setattr(cache, "_profile_status", lambda _args: status)
    monkeypatch.setattr(cache, "_daemon_environment", lambda _status: environment)
    monkeypatch.setattr(cache, "_run", execute)

    with pytest.raises(RuntimeError, match="task daemon status response is invalid"):
        cache._native_cache_checkout(args, tmp_path / "run", environment, (), status)
    assert commands == [["multica", "daemon", "status", "--output", "json"]]


@pytest.mark.unit
def test_native_checkout_rejects_a_stale_candidate_daemon_binding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MULTICA_DAEMON_PORT", "41001")
    monkeypatch.setenv("MULTICA_TOKEN", "task-token-fixture")
    monkeypatch.setenv("MULTICA_TASK_ID", "task-fixture")
    monkeypatch.setenv("MULTICA_WORKSPACE_ID", "workspace-fixture")
    monkeypatch.chdir(tmp_path)
    args = Namespace(
        multica="multica",
        git="git",
        profile="isolated",
        repository_a="https://gitlab.example/team/repo",
    )
    candidate = {"profile": "isolated", "status": "running", "pid": 42}
    stale = {"profile": "isolated", "status": "running", "pid": 99}
    cache_environment = {
        "MULTICA_TASK_ID": "task-fixture",
        "MULTICA_WORKSPACE_ID": "workspace-fixture",
    }
    monkeypatch.setattr(cache, "_profile_status", lambda _args: stale)
    run_mock = Mock(side_effect=[RuntimeError("task daemon status unavailable")])
    monkeypatch.setattr(cache, "_run", run_mock)

    with pytest.raises(RuntimeError, match="generation changed"):
        cache._native_cache_checkout(args, tmp_path / "run", cache_environment, (), candidate)
    run_mock.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("binding_key", "binding_value", "message"),
    [
        ("MULTICA_TASK_ID", "foreign-task", "task binding does not match"),
        ("ODCLI_GITLAB_PURPOSE", "task", "cache binding does not match"),
        ("MULTICA_DAEMON_PORT", "42001", "does not match the candidate daemon"),
    ],
)
def test_native_checkout_rejects_foreign_daemon_bindings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    binding_key: str,
    binding_value: str,
    message: str,
) -> None:
    monkeypatch.setenv("MULTICA_DAEMON_PORT", "41001")
    monkeypatch.setenv("MULTICA_TOKEN", "task-token-fixture")
    monkeypatch.setenv("MULTICA_TASK_ID", "task-fixture")
    monkeypatch.setenv("MULTICA_WORKSPACE_ID", "workspace-fixture")
    monkeypatch.chdir(tmp_path)
    args = Namespace(
        multica="multica",
        git="git",
        profile="isolated",
        repository_a="https://gitlab.example/team/repo",
    )
    status = {"profile": "isolated", "status": "running", "pid": 42}
    expected_environment = {
        "MULTICA_TASK_ID": "task-fixture",
        "MULTICA_WORKSPACE_ID": "workspace-fixture",
        "ODCLI_GITLAB_PURPOSE": "cache-read",
        "ODCLI_GITLAB_MEMBER_ID": "member-fixture",
        "ODCLI_GITLAB_SELECTED_HOST": "gitlab.example",
        "GIT_CONFIG_VALUE_1": "odcli",
    }
    daemon_environment = expected_environment | {"MULTICA_DAEMON_PORT": "41001"}
    daemon_environment[binding_key] = binding_value
    monkeypatch.setattr(cache, "_profile_status", lambda _args: status)
    monkeypatch.setattr(cache, "_daemon_environment", lambda _status: daemon_environment)
    run_mock = Mock(return_value=SimpleNamespace(stdout=json.dumps(status)))
    monkeypatch.setattr(cache, "_run", run_mock)

    with pytest.raises(RuntimeError, match=message):
        cache._native_cache_checkout(args, tmp_path / "run", expected_environment, (), status)
    assert run_mock.call_count == 1
    assert run_mock.call_args.args[0] == ["multica", "daemon", "status", "--output", "json"]


@pytest.mark.unit
def test_native_checkout_preserves_public_cli_refusal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MULTICA_DAEMON_PORT", "41001")
    monkeypatch.setenv("MULTICA_TOKEN", "task-token-fixture")
    monkeypatch.setenv("MULTICA_TASK_ID", "task-fixture")
    monkeypatch.setenv("MULTICA_WORKSPACE_ID", "workspace-fixture")
    monkeypatch.chdir(tmp_path)
    args = Namespace(
        multica="multica",
        git="git",
        profile="isolated",
        repository_a="https://gitlab.example/team/repo",
    )
    status = {"profile": "isolated", "status": "running", "pid": 42}
    environment = {
        "MULTICA_TASK_ID": "task-fixture",
        "MULTICA_WORKSPACE_ID": "workspace-fixture",
        "ODCLI_GITLAB_PURPOSE": "cache-read",
        "ODCLI_GITLAB_MEMBER_ID": "member-fixture",
        "ODCLI_GITLAB_SELECTED_HOST": "gitlab.example",
        "GIT_CONFIG_VALUE_1": "odcli",
    }
    run_mock = Mock(
        side_effect=[
            SimpleNamespace(stdout=json.dumps(status)),
            RuntimeError("command failed (1): multica checkout refused"),
        ]
    )
    monkeypatch.setattr(cache, "_profile_status", lambda _args: status)
    monkeypatch.setattr(cache, "_daemon_environment", lambda _status: environment)
    monkeypatch.setattr(cache, "_run", run_mock)

    with pytest.raises(RuntimeError, match="checkout refused"):
        cache._native_cache_checkout(args, tmp_path / "run", environment, (), status)
    checkout_call = run_mock.call_args_list[1]
    assert checkout_call.args[0] == ["multica", "repo", "checkout", args.repository_a]
    assert checkout_call.kwargs["cwd"] == tmp_path / "run" / "native-cache-checkout"
    assert checkout_call.kwargs["env"]["MULTICA_DAEMON_PORT"] == "41001"
    assert checkout_call.kwargs["env"]["MULTICA_TOKEN"] == "task-token-fixture"
    assert checkout_call.kwargs["env"]["MULTICA_TASK_ID"] == "task-fixture"
    assert checkout_call.kwargs["env"]["MULTICA_WORKSPACE_ID"] == "workspace-fixture"


@pytest.mark.unit
def test_public_member_refusal_accepts_only_nonzero_secret_free_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = Namespace(
        odcli_multica="odcli-multica",
        core_project=Path("/core"),
        multica_project="project",
        profile="isolated",
        workspace_id="workspace",
        repository_a="https://gitlab.example/team/repo",
        issue_b="MYL-2",
        run_b="run-2",
    )

    run_mock = Mock(
        return_value=SimpleNamespace(returncode=1, stdout="", stderr="member unavailable")
    )
    monkeypatch.setattr(subprocess, "run", run_mock)
    cache._assert_public_member_refusal(
        args,
        checkout=Path("/checkout"),
        member_id="invalid-member-fixture",
        secret_values=("secret-fixture",),
        prior_environment={},
    )

    assert run_mock.call_count == 1
    assert run_mock.call_args.args[0][-6:] == [
        "--member-id",
        "invalid-member-fixture",
        "/checkout",
        "--format",
        "json",
        "--dry-run",
    ]

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    with pytest.raises(RuntimeError, match="fail-closed"):
        cache._assert_public_member_refusal(
            args,
            checkout=Path("/checkout"),
            member_id="invalid-member-fixture",
            secret_values=("secret-fixture",),
            prior_environment={},
        )


@pytest.mark.unit
def test_cache_negative_fixtures_are_all_executed_through_git_fill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = Namespace(
        git="git",
        helper_dir=Path("/owned/bin"),
        cache_host="gitlab.example",
        cache_member_id="00000000-0000-4000-8000-000000000016",
        cache_owner_mismatch_member="00000000-0000-4000-8000-000000000017",
        cache_inactive_member="00000000-0000-4000-8000-000000000018",
        cache_broader_scope_member="00000000-0000-4000-8000-000000000019",
        cache_granular_scope_member="00000000-0000-4000-8000-000000000020",
        cache_incompatible_member="00000000-0000-4000-8000-000000000021",
    )
    calls: list[tuple[list[str], str, dict[str, str]]] = []

    def execute(
        argv: list[str], *, input: str, env: dict[str, str], **_kwargs: object
    ) -> SimpleNamespace:
        calls.append((argv, input, env))
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", execute)
    selected = {
        args.cache_member_id,
        args.cache_owner_mismatch_member,
        args.cache_inactive_member,
        args.cache_broader_scope_member,
        args.cache_granular_scope_member,
        args.cache_incompatible_member,
    }
    monkeypatch.setattr(
        cache,
        "_installed_helper_provenance",
        lambda *_args: Path("/owned/bin/git-credential-odcli"),
    )
    monkeypatch.setattr(
        cache,
        "read_gitlab_credential_mappings",
        lambda: tuple(
            SimpleNamespace(user_id=member, host="gitlab.example", token_key=f"TOKEN_{member}")
            for member in selected
        ),
    )
    evidence: list[dict[str, str | int | bool]] = []
    cache._assert_cache_helper_negative_inputs(
        args,
        {
            "ODCLI_GITLAB_PURPOSE": "cache-read",
            "ODCLI_GITLAB_SELECTED_HOST": "gitlab.example",
        },
        ("secret-fixture",),
        evidence,
        Path("/owned/bin/git-credential-odcli"),
    )

    assert len(calls) == 8
    assert calls[0][1] == "protocol=https\nhost=wrong.example\n\n"
    assert calls[1][1] == "protocol=https\nhost=gitlab.example\nhost=wrong.example\n\n"
    assert "ODCLI_GITLAB_PURPOSE" not in calls[2][2]
    assert [call[2]["ODCLI_GITLAB_MEMBER_ID"] for call in calls[3:]] == [
        args.cache_owner_mismatch_member,
        args.cache_inactive_member,
        args.cache_broader_scope_member,
        args.cache_granular_scope_member,
        args.cache_incompatible_member,
    ]
    assert evidence == [
        {"case": "C1", "negative_inputs": 3, "ok": True},
        {"case": "C1", "rejected_fixture": "owner_mismatch", "ok": True},
        {"case": "C1", "rejected_fixture": "inactive_token", "ok": True},
        {"case": "C1", "rejected_fixture": "broader_scope", "ok": True},
        {"case": "C1", "rejected_fixture": "granular_scope", "ok": True},
        {"case": "C1", "rejected_fixture": "incompatible_response", "ok": True},
    ]


@pytest.mark.unit
def test_rollback_starts_prior_generation_without_replacement_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = Namespace()
    events: list[str] = []
    cache_environment = _rollback_fixture(monkeypatch, events, {"GIT_CONFIG_VALUE_1": ""})

    cache._rollback_cache_generation(args, {}, cache_environment)

    assert events == ["stop", "start-prior"]


@pytest.mark.unit
def test_rollback_rejects_replacement_generation_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = Namespace()
    events: list[str] = []
    cache_environment = _rollback_fixture(monkeypatch, events, {"GIT_CONFIG_VALUE_1": "odcli"})

    with pytest.raises(RuntimeError, match="inherited replacement"):
        cache._rollback_cache_generation(args, {}, cache_environment)

    assert events == ["stop", "start-prior"]


def _rollback_fixture(
    monkeypatch: pytest.MonkeyPatch, events: list[str], inherited: dict[str, str]
) -> dict[str, str]:
    def start_prior(_args: Namespace, _environment: dict[str, str]) -> dict[str, object]:
        events.append("start-prior")
        return {"pid": 42}

    monkeypatch.setattr(cache, "_profile_stop", lambda _args: events.append("stop"))
    monkeypatch.setattr(cache, "_profile_start", start_prior)
    monkeypatch.setattr(cache, "_daemon_environment", lambda _status: inherited)
    return {
        "ODCLI_GITLAB_PURPOSE": "cache-read",
        "ODCLI_GITLAB_MEMBER_ID": "cache-member",
        "ODCLI_GITLAB_SELECTED_HOST": "gitlab.example",
        "GIT_CONFIG_VALUE_1": "odcli",
    }
