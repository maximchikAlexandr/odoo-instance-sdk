from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

import build_assets
import build_process
import scripts.e2e_gitlab_member_workflow as workflow
import scripts.e2e_update_dashboard as update
from scripts.e2e_gitlab_member_workflow import _sanitize
from tests.integration.real_odoo.contracts import CANONICAL_LEAF_COUNT
from tests.unit.test_cli_output_modes import PUBLIC_LEAF_CASES

ROOT = Path(__file__).parents[2]


@pytest.mark.unit
@pytest.mark.parametrize(
    "script",
    [
        ROOT / "scripts" / "e2e_gitlab_member_workflow.py",
        ROOT / "scripts" / "e2e_update_dashboard.py",
    ],
)
def test_private_e2e_scripts_have_bounded_help(script: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "cleanup" in result.stdout.lower()


@pytest.mark.unit
def test_gitlab_workflow_assets_are_parameterized_and_sanitized() -> None:
    script = (ROOT / "scripts" / "e2e_gitlab_member_workflow.py").read_text(encoding="utf-8")
    assert "TEST_REPOSITORY_URL" in script
    assert "TEST_GITLAB_MEMBER" in script
    assert "TEST_GITLAB_TICKET_A" in script
    assert "--ticket-a" in script
    assert "TEST_REPOSITORY_B_URL" in script
    assert "TEST_REPOSITORY_B_OURS_URL" in script
    assert "TEST_REPOSITORY_B_ODOO_URL" in script
    assert "TEST_REPOSITORY_INDEPENDENT_URL" in script
    assert "--token-key-a" not in script
    assert "--token-key-b" not in script
    assert "TEST_GITLAB_TOKEN_KEY_A" not in script
    assert "TEST_GITLAB_TOKEN_KEY_B" not in script
    assert "read_gitlab_credential_mappings" in script
    assert "read_gitlab_environment" in script
    assert "PRIVATE-TOKEN" not in script
    assert "shutil.rmtree(root" in script
    assert '"git",\n        "mr",\n        "create"' in script
    assert "--recurse-submodules" in script


@pytest.mark.unit
@pytest.mark.parametrize("key", ("private-token", "password", "token", "authorization"))
@pytest.mark.parametrize("separator", (":", "="))
def test_gitlab_sanitizer_redacts_delimited_secret_values(key: str, separator: str) -> None:
    canary = f"canary-{key}-{separator}-value"
    sanitized = _sanitize(f"{key.upper()}{separator} {canary}")

    assert canary not in sanitized
    assert "<redacted>" in sanitized


@pytest.mark.unit
def test_gitlab_secret_corpus_reads_selected_owner_only_value_and_scrubs_child_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    member = "11111111-1111-4111-8111-111111111111"
    user_root = tmp_path / "user-root"
    credentials = user_root / "credentials"
    credentials.mkdir(mode=0o700, parents=True)
    (credentials / ".env").write_text(
        "ODCLI_GITLAB_TOKEN_SELECTED=selected-secret\n", encoding="utf-8"
    )
    (credentials / ".env").chmod(0o600)
    (credentials / "gitlab-credentials.toml").write_text(
        "[[mappings]]\n"
        f'user_id = "{member}"\n'
        'host = "gitlab.example"\n'
        'login = "selected"\n'
        'token_key = "ODCLI_GITLAB_TOKEN_SELECTED"\n',
        encoding="utf-8",
    )
    (credentials / "gitlab-credentials.toml").chmod(0o600)
    monkeypatch.setenv("ODCLI_USER_ROOT", str(user_root))
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.gitlab_credentials.get_user_root",
        lambda **_: user_root,
    )

    secrets = workflow._secret_corpus(member)
    child_output = "child output selected-secret without a key prefix"
    summary = _sanitize(child_output, secrets=secrets)

    assert secrets == ("selected-secret",)
    assert "selected-secret" not in summary
    assert "<redacted>" in summary


def test_gitlab_commit_asset_scopes_ticket_to_initialized_a_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = Namespace(odcli="odcli", git="git")
    calls: list[list[str]] = []
    policy_outputs: list[list[str]] = []

    def fake_git(git: str, root: Path, *git_args: str, secrets: tuple[str, ...] = ()) -> str:
        del git, root, secrets
        return ""

    def fake_cli(
        executable: str,
        command: list[str],
        *,
        cwd: Path | None,
        evidence: list[dict[str, str | int | bool]],
        secrets: tuple[str, ...],
    ) -> dict[str, object]:
        del executable, cwd, evidence, secrets
        calls.append(command)
        ticket = command[command.index("--ticket") + 1] if "--ticket" in command else None
        message = "[IMP] addon: verify"
        if ticket is not None:
            message = f"[IMP] addon: {ticket} verify"
        output_args = ["git", "commit", "-m", message]
        policy_outputs.append(output_args)
        return {"ok": True, "result": {"args": output_args}}

    monkeypatch.setattr(workflow, "_git", fake_git)
    monkeypatch.setattr(workflow, "_cli", fake_cli)
    evidence: list[dict[str, str | int | bool]] = []
    workflow._commit(
        args,
        tmp_path / "a",
        "member",
        label="A",
        ticket="MYL-481",
        evidence=evidence,
        secrets=(),
    )
    workflow._commit(
        args,
        tmp_path / "b-ours",
        "member",
        label="B-ours",
        ticket=None,
        evidence=evidence,
        secrets=(),
    )

    assert "--ticket" in calls[0]
    assert calls[0][calls[0].index("--ticket") + 1] == "MYL-481"
    assert "--ticket" not in calls[1]
    assert "MYL-481" in policy_outputs[0][-1]
    assert "MYL-481" not in policy_outputs[1][-1]


def _init_git_checkout(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "tests"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "tests@example.test"], check=True
    )


def test_gitlab_mr_allows_its_expected_untracked_inputs_and_preserves_three_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    _init_git_checkout(checkout)
    args = Namespace(odcli="odcli", git="git", target="main")
    calls: list[list[str]] = []
    images: list[bytes] = []

    def fake_cli(
        executable: str,
        command: list[str],
        *,
        cwd: Path | None,
        evidence: list[dict[str, str | int | bool]],
        secrets: tuple[str, ...],
    ) -> dict[str, object]:
        del executable, cwd, evidence, secrets
        calls.append(command)
        images.append((checkout / "image-A.png").read_bytes())
        return {"ok": True, "result": {}}

    monkeypatch.setattr(workflow, "_cli", fake_cli)
    workflow._mr(args, checkout, "member", label="A", evidence=[], secrets=())

    assert len(calls) == 3
    assert images[0] == images[1]
    assert images[1] != images[2]
    assert (checkout / "description-A.md").is_file()
    assert (checkout / "image-A.png").is_file()
    assert (
        subprocess.run(
            ["git", "-C", str(checkout), "ls-files", "--error-unmatch", "image-A.png"],
            capture_output=True,
            check=False,
        ).returncode
        == 1
    )


@pytest.mark.parametrize("tracked_mode", ("staged", "tracked"))
def test_gitlab_mr_rejects_image_in_index_or_tracked_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tracked_mode: str
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    _init_git_checkout(checkout)
    if tracked_mode == "tracked":
        (checkout / "image-A.png").write_bytes(b"old image")
        subprocess.run(["git", "-C", str(checkout), "add", "image-A.png"], check=True)
        subprocess.run(["git", "-C", str(checkout), "commit", "-qm", "tracked image"], check=True)
    args = Namespace(odcli="odcli", git="git", target="main")
    calls = 0

    def fake_cli(
        executable: str,
        command: list[str],
        *,
        cwd: Path | None,
        evidence: list[dict[str, str | int | bool]],
        secrets: tuple[str, ...],
    ) -> dict[str, object]:
        nonlocal calls
        del executable, command, cwd, evidence, secrets
        calls += 1
        if tracked_mode == "staged" and calls == 1:
            subprocess.run(["git", "-C", str(checkout), "add", "image-A.png"], check=True)
        return {"ok": True, "result": {}}

    monkeypatch.setattr(workflow, "_cli", fake_cli)
    with pytest.raises(RuntimeError, match="tracked/index"):
        workflow._mr(args, checkout, "member", label="A", evidence=[], secrets=())
    assert calls == 3


def test_relative_gitmodules_roles_resolve_against_b_origin_before_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "b"
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    (checkout / ".gitmodules").write_text(
        '[submodule "ours"]\n'
        "\tpath = vendor/ours\n"
        "\turl = ../ours-addon.git\n"
        '[submodule "odoo"]\n'
        "\tpath = vendor/odoo\n"
        "\turl = ../odoo-addon.git\n",
        encoding="utf-8",
    )
    roles = {
        "B": "https://gitlab.example/team/b.git",
        "B/ours": "https://gitlab.example/team/ours-addon.git",
        "B/odoo": "https://gitlab.example/team/odoo-addon.git",
    }
    args = Namespace(git="git")
    calls: list[tuple[str, ...]] = []
    real_git = workflow._git

    def fake_git(git: str, root: Path, *git_args: str, secrets: tuple[str, ...] = ()) -> str:
        calls.append(git_args)
        if git_args[:2] == ("submodule", "status"):
            return "-a ours\n-b odoo"
        return real_git(git, root, *git_args, secrets=secrets)

    monkeypatch.setattr(workflow, "_git", fake_git)
    assert workflow._repository_identity(
        "../ours-addon.git", base=roles["B"]
    ) == workflow._repository_identity(roles["B/ours"])
    workflow._assert_recursive_topology(args, checkout, roles, secrets=())

    assert any(call[:2] == ("submodule", "status") for call in calls)


def test_relative_gitmodules_role_mismatch_fails_before_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "b"
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    (checkout / ".gitmodules").write_text(
        '[submodule "ours"]\n\turl = ../ours-addon.git\n'
        '[submodule "odoo"]\n\turl = ../odoo-addon.git\n',
        encoding="utf-8",
    )
    roles = {
        "B": "https://gitlab.example/team/b.git",
        "B/ours": "https://gitlab.example/team/ours-addon.git",
        "B/odoo": "https://gitlab.example/team/other-project.git",
    }
    args = Namespace(git="git")
    calls: list[tuple[str, ...]] = []
    real_git = workflow._git

    def fake_git(git: str, root: Path, *git_args: str, secrets: tuple[str, ...] = ()) -> str:
        calls.append(git_args)
        if git_args[:2] == ("submodule", "status"):
            raise AssertionError("status must not be inspected after topology mismatch")
        return real_git(git, root, *git_args, secrets=secrets)

    monkeypatch.setattr(workflow, "_git", fake_git)
    with pytest.raises(RuntimeError, match="both supplied submodule roles"):
        workflow._assert_recursive_topology(args, checkout, roles, secrets=())

    assert not any(call[:2] == ("submodule", "status") for call in calls)


@pytest.mark.unit
def test_update_workflow_retains_exact_revision_and_dashboard_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = (ROOT / "scripts" / "e2e_update_dashboard.py").read_text(encoding="utf-8")
    assert "--baseline-sha" in script
    assert "--updater-sha" in script
    assert "--candidate-sha" in script
    assert "odoo-instance-sdk[dashboard]" in script
    assert "starlette_admin" in script
    assert "sqlcipher3" in script
    assert "admin_imports" in script
    assert "historical_baseline_sha" in script
    assert "updater_source_sha" in script
    assert "expected_dependency_loss" in script
    assert "updated_with_extra" in script
    assert "--ref" in script
    assert '"/healthz"' in script
    assert '"/openapi.json"' in script
    assert "multica" in script and "daemon" in script
    assert "UV_TOOL_DIR" in script
    assert "UV_TOOL_BIN_DIR" in script
    assert '"--tool-dir"' not in script

    web_root = tmp_path / "web"
    dist = web_root / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("stale", encoding="utf-8")
    (assets / "stale.js").write_text("stale", encoding="utf-8")
    calls: list[tuple[str, ...]] = []

    def build(argv: tuple[str, ...], *, cwd: Path) -> None:
        del cwd
        calls.append(argv)
        if argv == ("npm", "run", "build"):
            (dist / "index.html").write_text("current", encoding="utf-8")
            (assets / "current.js").write_text("current", encoding="utf-8")

    monkeypatch.setattr(build_assets, "run_build_process", build)
    build_assets.ensure_dashboard_assets.cache_clear()
    try:
        build_assets.ensure_dashboard_assets(web_root, dist)
        build_assets.ensure_dashboard_assets(web_root, dist)
    finally:
        build_assets.ensure_dashboard_assets.cache_clear()
    assert calls == [("npm", "ci"), ("npm", "run", "build")]
    assert (dist / "index.html").read_text(encoding="utf-8") == "current"
    assert (assets / "current.js").is_file()


@pytest.mark.unit
def test_build_process_allowlists_platform_inputs_and_cleans_owned_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = tmp_path / "child-environment.json"
    caller_home = tmp_path / "caller-home"
    canaries = {
        "ODCLI_GITLAB_ASKPASS_FILE": "askpass-secret-canary",
        "GIT_ASKPASS": "git-askpass-secret-canary",
        "GOOGLE_APPLICATION_CREDENTIALS": "google-secret-canary",
        "NPM_CONFIG_REGISTRY": "https://user:secret@example.invalid/npm/",
        "CI_JOB_JWT_V2": "jwt-secret-canary",
        "BUILD_CANARY_WITHOUT_SECRET_NAME": "arbitrary-secret-canary",
    }
    monkeypatch.setenv("HOME", str(caller_home))
    for key, value in canaries.items():
        monkeypatch.setenv(key, value)

    child = (
        "import json, os\n"
        f"with open({str(report)!r}, 'w', encoding='utf-8') as stream:\n"
        "    json.dump(dict(os.environ), stream)\n"
    )
    build_process.run_build_process((sys.executable, "-c", child), cwd=tmp_path)

    environment = json.loads(report.read_text(encoding="utf-8"))
    assert environment["PATH"] == os.environ["PATH"]
    assert environment["HOME"] != str(caller_home)
    assert Path(environment["NPM_CONFIG_CACHE"]).parent == Path(environment["HOME"]).parent
    assert not Path(environment["HOME"]).exists()
    assert not Path(environment["NPM_CONFIG_CACHE"]).exists()
    assert all(key not in environment for key in canaries)


@pytest.mark.unit
def test_update_composition_helpers_are_bounded_and_secret_free(tmp_path: Path) -> None:
    tool_dir = tmp_path / "tool"
    receipt = tool_dir / "odoo-instance-sdk" / "uv-receipt.toml"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(
        '[tool]\nrequirements = [{ name = "odoo-instance-sdk", extras = ["dashboard"] }]\n',
        encoding="utf-8",
    )

    assert update._receipt_has_dashboard(tool_dir)
    receipt.write_text(
        '[tool]\nrequirements = [{ name = "odoo-instance-sdk" }]\n',
        encoding="utf-8",
    )
    assert not update._receipt_has_dashboard(tool_dir)


@pytest.mark.unit
def test_update_install_contract_uses_isolated_uv_tool_root_and_cleans_up(
    tmp_path: Path,
) -> None:
    root = tmp_path / "isolated-update"
    tool_dir = root / "tool"
    tool_bin = root / "bin"
    home = root / "home"
    cache = root / "cache"
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "UV_CACHE_DIR": str(cache),
            "ODCLI_USER_ROOT": str(root / "user"),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local" / "share"),
        }
    )
    requirement = f"odoo-instance-sdk @ file://{ROOT}"
    fake_uv = tmp_path / "uv"
    fake_uv.write_text(
        f"#!{sys.executable}\n"
        "import os\n"
        "import sys\n"
        "from pathlib import Path\n"
        "if sys.argv[1:3] != ['tool', 'install']:\n"
        "    raise SystemExit('unexpected install shape')\n"
        "requirement = sys.argv[-1]\n"
        "if not requirement.startswith('odoo-instance-sdk @ file://'):\n"
        "    raise SystemExit('source requirement missing')\n"
        "tool_dir = Path(os.environ['UV_TOOL_DIR'])\n"
        "tool_bin = Path(os.environ['UV_TOOL_BIN_DIR'])\n"
        "package = tool_dir / 'odoo-instance-sdk'\n"
        "(package / 'bin').mkdir(parents=True)\n"
        "(package / 'source-requirement.txt').write_text(requirement, encoding='utf-8')\n"
        "odcli = package / 'bin' / 'odcli'\n"
        f"odcli.write_text('#!{sys.executable}\\nprint(\\\"odcli, version 0.1.0\\\")\\n', encoding='utf-8')\n"
        "odcli.chmod(0o700)\n"
        "tool_bin.mkdir(parents=True)\n"
        "launcher = tool_bin / 'odcli'\n"
        "launcher.write_text(odcli.read_text(encoding='utf-8'), encoding='utf-8')\n"
        "launcher.chmod(0o700)\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o700)

    try:
        update._install_tool(
            str(fake_uv),
            requirement,
            tool_dir=tool_dir,
            tool_bin=tool_bin,
            env=env,
        )
        odcli = update._require_executable(tool_bin / "odcli")
        version = subprocess.run(
            [str(odcli), "--version"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert version.returncode == 0, version.stderr
        assert version.stdout.startswith("odcli, version ")
        assert (tool_dir / "odoo-instance-sdk" / "source-requirement.txt").read_text(
            encoding="utf-8"
        ) == requirement
        assert not (home / ".local" / "share" / "uv" / "tools").exists()
        assert not (home / ".local" / "bin").exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)

    assert not root.exists()


@pytest.mark.unit
def test_update_install_timeout_is_bounded_and_cleans_up_owned_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "timed-out-update"

    with pytest.raises(RuntimeError, match=r"command timed out after 0\.01 seconds"):
        update._run(
            [sys.executable, "-c", "import time; time.sleep(1)"],
            env=os.environ.copy(),
            timeout=0.01,
        )

    def fail_run(
        argv: list[str], *, env: dict[str, str], timeout: float
    ) -> subprocess.CompletedProcess[str]:
        del argv, env, timeout
        raise RuntimeError("command timed out after 180 seconds")

    monkeypatch.setattr(update, "_run", fail_run)
    result = update.main(
        [
            "--baseline-sha",
            "baseline",
            "--candidate-sha",
            "candidate",
            "--work-root",
            str(root),
        ]
    )

    assert result == 1
    assert "timed out after 180 seconds" in capsys.readouterr().out
    assert not root.exists()


@pytest.mark.unit
def test_public_matrix_includes_new_gitlab_leaves() -> None:
    paths = {" ".join(case.path) for case in PUBLIC_LEAF_CASES}
    assert {"git clone", "git mr create"} <= paths
    assert len(PUBLIC_LEAF_CASES) == CANONICAL_LEAF_COUNT

    matrix = (ROOT / "tests/integration/real_odoo/command-matrix.md").read_text(encoding="utf-8")
    assert "| `git clone` |" in matrix
    assert "| `git mr create` |" in matrix


@pytest.mark.unit
def test_readme_states_generic_git_boundaries_and_credential_schema() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for required in (
        "gitlab-credentials.toml",
        "ODCLI_GITLAB_TOKEN_*",
        "no GitHub support",
        "no implicit",
        "no standalone `push` command",
        "no hidden superproject",
        "`odcli git clone`",
        "`odcli git mr create`",
    ):
        assert required in readme


@pytest.mark.unit
def test_readme_documents_admin_secret_and_backup_freshness_contract() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for required in (
        "Optional administrator and catalogue",
        "~/.odcli/config/admin.toml",
        "~/.odcli/config/secrets.key",
        "~/.odcli/secrets.sqlite3",
        "ODCLI_SECRET_KEY_FILE",
        "write-only",
        "record revisions",
        "Backup download date unknown",
        "completed catalogue restore",
        "no second catalogue request",
    ):
        assert required in readme
    assert "master_password =" not in readme
    assert "private-token =" not in readme
