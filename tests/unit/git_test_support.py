from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.exceptions import GitLabCredentialError
from odoo_instance_sdk.execution import JsonValue
from odoo_instance_sdk.internal import proc
from odoo_instance_sdk.internal.gitlab_credentials import ensure_credential_directory
from odoo_instance_sdk.models import (
    GitLabCredentialContext,
    GitLabCredentialIdentity,
    GitLabIdentity,
    StartConfig,
)
from odoo_instance_sdk.resources.instance import OdooInstance
from odoo_instance_sdk.resources.module import ModuleResource

MEMBER = "11111111-1111-4111-8111-111111111111"
OTHER_MEMBER = "22222222-2222-4222-8222-222222222222"
HOST = "gitlab.example"
OTHER_HOST = "gitlab.other.example"
THIRD_HOST = "gitlab.third.example"


def _credential_resolver(
    credential: GitLabCredentialContext,
    identity: GitLabIdentity,
    requested_hosts: list[str],
) -> Callable[..., tuple[GitLabCredentialContext, GitLabIdentity]]:
    responses = {credential.host: (credential, identity)}

    def resolve(
        _member_id: str, host: str, **_kwargs: object
    ) -> tuple[GitLabCredentialContext, GitLabIdentity]:
        requested_hosts.append(host)
        try:
            return responses[host]
        except KeyError:
            raise GitLabCredentialError("stale extra-host token") from None

    return resolve


def _credential_resolver_for_hosts(
    responses: Mapping[str, tuple[GitLabCredentialContext, GitLabIdentity]],
    requested_hosts: list[str],
) -> Callable[..., tuple[GitLabCredentialContext, GitLabIdentity]]:
    def resolve(
        _member_id: str, host: str, **_kwargs: object
    ) -> tuple[GitLabCredentialContext, GitLabIdentity]:
        requested_hosts.append(host)
        try:
            return responses[host]
        except KeyError:
            raise GitLabCredentialError(f"unmapped host: {host}") from None

    return resolve


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
        env=os.environ | {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull},
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _repo(root: Path, *, origin: str = "https://gitlab.example/team/repo") -> None:
    root.mkdir(parents=True)
    _git(root, "init", "-q", "-b", "feature")
    _git(root, "config", "user.email", "tests@example.test")
    _git(root, "config", "user.name", "tests")
    _git(root, "remote", "add", "origin", origin)
    (root / "README.md").write_text("base\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-qm", "base")


def _instance(root: Path) -> OdooInstance:
    config = InstanceConfig(
        base_url="http://127.0.0.1:8069",
        default_cwd=root,
        start_config=StartConfig(addons_path=["addons"]),
    )
    instance = cast("OdooInstance", SimpleNamespace(config=config))
    instance.modules = ModuleResource(instance)
    return instance


def _credential(
    host: str = HOST, *, login: str = "alice", token: str = "token"
) -> tuple[GitLabCredentialContext, GitLabIdentity]:
    credential = GitLabCredentialContext(
        identity=GitLabCredentialIdentity(
            user_id=MEMBER,
            host=host,
            login=login,
            token_key="ODCLI_GITLAB_TOKEN_TEST",
        ),
        _token=token,
    )
    identity = GitLabIdentity(
        user_id=7, username=login, name="Alice Example", email="alice@example.test", host=host
    )
    return credential, identity


def _mapping(
    root: Path,
    hosts: tuple[str, ...],
    *,
    member_id: str = MEMBER,
    append: bool = False,
) -> None:
    directory = ensure_credential_directory(root)
    mapping_path = directory / "gitlab-credentials.toml"
    environment_path = directory / ".env"
    existing_entries = mapping_path.read_text(encoding="utf-8") if append else ""
    existing_values = environment_path.read_text(encoding="utf-8").splitlines() if append else []
    entries = [existing_entries.rstrip()] if existing_entries.strip() else []
    values = list(existing_values)
    offset = len(values)
    for index, host in enumerate(hosts, start=offset):
        key = f"ODCLI_GITLAB_TOKEN_{index}"
        entries.append(
            "[[mappings]]\n"
            f'user_id = "{member_id}"\n'
            f'host = "{host}"\n'
            f'login = "user-{index}"\n'
            f'token_key = "{key}"\n'
        )
        values.append(f"{key}=token-{index}")
    mapping_path.write_text("\n".join(entries), encoding="utf-8")
    mapping_path.chmod(0o600)
    environment_path.write_text("\n".join(values) + "\n", encoding="utf-8")
    environment_path.chmod(0o600)


def _identity_transport(host: str, _token: str) -> dict[str, JsonValue]:
    usernames = {HOST: "user-0", OTHER_HOST: "user-1", THIRD_HOST: "user-2"}
    return {
        "id": 7,
        "username": usernames[host],
        "name": "Selected Member",
        "commit_email": "selected@example.test",
    }


def _add_local_submodule(
    parent: Path, source: Path, path: str, url: str, *, logical_name: str | None = None
) -> None:
    _git(
        parent,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        str(source),
        path,
    )
    _git(parent, "config", "-f", ".gitmodules", f"submodule.{path}.url", url)
    if logical_name is not None:
        _git(
            parent,
            "config",
            "-f",
            ".gitmodules",
            "--rename-section",
            f"submodule.{path}",
            f"submodule.{logical_name}",
        )
    _git(parent, "add", ".gitmodules", path)
    _git(parent, "commit", "-qm", f"add {path}")


def _recursive_sources(
    tmp_path: Path, *, hostile: bool = False
) -> tuple[Path, Path, Path, Path, Path]:
    leaf = tmp_path / "leaf-source"
    _repo(leaf, origin="https://gitlab.example/team/leaf")
    deepest = tmp_path / "deepest-source"
    _repo(deepest, origin="https://gitlab.third.example/team/deepest")
    ultimate = tmp_path / "ultimate-source"
    _repo(ultimate, origin="https://gitlab.third.example/team/ultimate")
    _add_local_submodule(
        deepest, ultimate, "ultimate", "https://gitlab.third.example/team/ultimate"
    )
    _add_local_submodule(
        leaf,
        deepest,
        "vendor/lib",
        "https://gitlab.third.example/team/deepest",
        logical_name="nested-deep",
    )
    if hostile:
        _git(
            leaf,
            "config",
            "-f",
            ".gitmodules",
            "url.https://evil.example/.insteadOf",
            "https://gitlab.third.example/",
        )
        _git(leaf, "add", ".gitmodules")
        _git(leaf, "commit", "-qm", "hostile deepest policy")
    child = tmp_path / "child-source"
    _repo(child, origin="https://gitlab.other.example/team/child")
    _add_local_submodule(child, leaf, "leaf", "https://gitlab.example/team/leaf")
    root = tmp_path / "root-source"
    _repo(root, origin="https://gitlab.example/team/root")
    _add_local_submodule(root, child, "child", "https://gitlab.other.example/team/child")
    return root, child, leaf, deepest, ultimate


def _two_level_sources(tmp_path: Path) -> tuple[Path, Path, Path]:
    leaf = tmp_path / "two-level-leaf-source"
    _repo(leaf, origin="https://gitlab.example/team/leaf")
    child = tmp_path / "two-level-child-source"
    _repo(child, origin="https://gitlab.other.example/team/child")
    _add_local_submodule(child, leaf, "leaf", "https://gitlab.example/team/leaf")
    root = tmp_path / "two-level-root-source"
    _repo(root, origin="https://gitlab.example/team/root")
    _add_local_submodule(root, child, "child", "https://gitlab.other.example/team/child")
    return root, child, leaf


def _offline_rewrite_arguments(argv: Sequence[str], urls: Mapping[str, Path]) -> list[str]:
    result: list[str] = []
    for arg in argv:
        if not arg.startswith("url.https://") or ".insteadOf=" not in arg:
            continue
        base, prefix = arg.removeprefix("url.").split(".insteadOf=", 1)
        for url, source in urls.items():
            if url.startswith(base):
                suffix = url.removeprefix(base)
                for ending in (suffix, suffix + ".git"):
                    result.extend(["-c", f"url.{source.as_uri()}.insteadOf={prefix}{ending}"])
    return result


CloneResultPolicy = Callable[
    [str, Sequence[str], Path, Path, Path | None, bool], subprocess.CompletedProcess[str] | None
]


def _real_clone_executor(  # noqa: C901
    root_source: Path,
    child_source: Path,
    leaf_source: Path,
    deepest_source: Path | None = None,
    ultimate_source: Path | None = None,
    *,
    result_policy: CloneResultPolicy | None = None,
) -> tuple[type[object], list[str], list[dict[str, str]]]:
    urls = {
        "https://gitlab.example/team/child": child_source,
        "https://gitlab.other.example/team/child": child_source,
        "https://gitlab.example/team/leaf": leaf_source,
        "https://gitlab.other.example/team/leaf": leaf_source,
    }
    if deepest_source is not None and ultimate_source is not None:
        urls.update(
            {
                "https://gitlab.third.example/team/deepest": deepest_source,
                "https://gitlab.third.example/team/ultimate": ultimate_source,
            }
        )
    calls: list[str] = []
    environments: list[dict[str, str]] = []

    class RealExecutor:
        def execute(self, step: object, **_kwargs: object) -> proc.ProcessResult:  # noqa: C901
            step_id = getattr(step, "step_id")
            calls.append(step_id)
            argv = tuple(getattr(step, "argv"))
            if step_id == "git.clone":
                destination = Path(argv[-1])
                result = subprocess.run(
                    [
                        "git",
                        "clone",
                        *argv[argv.index("clone") + 1 : -2],
                        str(root_source),
                        str(destination),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode == 0:
                    _git(
                        destination,
                        "remote",
                        "set-url",
                        "origin",
                        "https://gitlab.example/team/root",
                    )
            else:
                factory = getattr(step, "runtime_environment_factory", None)
                if callable(factory):
                    runtime_environment, cleanup, _secrets = factory()
                else:
                    runtime_environment = tuple(getattr(step, "environment_overrides"))

                    def cleanup() -> None:
                        return None

                try:
                    runtime_values = dict(getattr(step, "environment")) | dict(runtime_environment)
                    environments.append(runtime_values)
                    command = ["git", "-c", "protocol.file.allow=always"]
                    for url, source in urls.items():
                        command.extend(
                            [
                                "-c",
                                f"url.{source.as_uri()}.insteadOf={url}",
                                "-c",
                                f"url.{source.as_uri()}.insteadOf={url}.git",
                            ]
                        )
                    command_args = list(argv[1:])
                    assert "protocol.ssh.allow=never" in command_args
                    assert "protocol.ext.allow=never" in command_args
                    command.extend(_offline_rewrite_arguments(command_args, urls))
                    command.extend(command_args)
                    if (
                        step_id == "git.clone.submodule-update-recursive"
                        and (
                            Path(getattr(step, "cwd")) / "child" / "vendor" / "lib"
                        ).parent.exists()
                        and not (
                            Path(getattr(step, "cwd")) / "child" / "vendor" / "lib" / ".git"
                        ).exists()
                    ):
                        subprocess.run(
                            [
                                "git",
                                "-C",
                                str(Path(getattr(step, "cwd")) / "child"),
                                "-c",
                                "protocol.file.allow=always",
                                "-c",
                                f"url.{leaf_source.as_uri()}.insteadOf=https://gitlab.other.example/",
                                "submodule",
                                "update",
                                "--init",
                                "--",
                                "vendor/lib",
                            ],
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                    policy_result = (
                        result_policy(
                            step_id,
                            tuple(command_args),
                            Path(getattr(step, "cwd")),
                            leaf_source,
                            deepest_source,
                            False,
                        )
                        if result_policy is not None
                        else None
                    )
                    result = policy_result or subprocess.run(
                        command,
                        capture_output=True,
                        text=True,
                        check=False,
                        env=os.environ | runtime_values,
                    )
                    if result_policy is not None:
                        result_policy(
                            step_id,
                            tuple(command_args),
                            Path(getattr(step, "cwd")),
                            leaf_source,
                            deepest_source,
                            True,
                        )
                    if step_id == "git.clone.submodule-status-final" and result.returncode == 0:
                        checkout_root = Path(getattr(step, "cwd"))
                        status_lines = []
                        for raw_line in result.stdout.splitlines(keepends=True):
                            line = raw_line
                            fields = (
                                raw_line[1:].strip().split(maxsplit=1)
                                if raw_line.startswith("-")
                                else []
                            )
                            if (
                                len(fields) == 2
                                and (checkout_root / fields[1].split(" ", 1)[0] / ".git").exists()
                            ):
                                line = f" {line[1:]}"
                            status_lines.append(line)
                        result = subprocess.CompletedProcess(
                            command, result.returncode, "".join(status_lines), result.stderr
                        )
                    if result.returncode == 0 and "submodule-update" in step_id:
                        checkout_root = Path(getattr(step, "cwd"))
                        for marker in checkout_root.rglob(".git"):
                            candidate = marker.parent
                            if candidate == checkout_root:
                                continue
                            source = child_source if candidate.name == "child" else leaf_source
                            origin = _git(source, "remote", "get-url", "origin").strip()
                            if candidate.name != "child":
                                parent_origin = _git(
                                    candidate.parent, "config", "remote.origin.url"
                                ).strip()
                                declared = subprocess.run(
                                    [
                                        "git",
                                        "-C",
                                        str(candidate.parent),
                                        "config",
                                        "-f",
                                        ".gitmodules",
                                        "--get-regexp",
                                        r"^submodule\\..*\\.url$",
                                    ],
                                    capture_output=True,
                                    text=True,
                                    check=False,
                                ).stdout
                                if "../leaf.git" in declared or parent_origin.startswith(
                                    "git@gitlab.other.example:"
                                ):
                                    origin = "https://gitlab.other.example/team/leaf"
                            if "vendor" in candidate.parts:
                                origin = origin.replace("gitlab.example", "gitlab.other.example")
                            _git(
                                candidate,
                                "remote",
                                "set-url",
                                "origin",
                                origin,
                            )
                    assert result.returncode == 0 or result_policy is not None, (
                        step_id,
                        command,
                        result.stderr,
                    )
                finally:
                    cleanup()
            return proc.ProcessResult(
                argv=argv,
                returncode=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                duration=0.0,
                cwd=getattr(step, "cwd"),
                environment=(),
            )

    return RealExecutor, calls, environments
