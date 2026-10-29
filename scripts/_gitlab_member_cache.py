"""Private support for the existing GitLab cache workflow runner.

This module has no CLI entrypoint; the public orchestration command remains
``scripts/e2e_gitlab_member_workflow.py --mode multica-cache``.
"""

from __future__ import annotations

import argparse
import base64
import configparser
import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from odoo_instance_sdk.internal.gitlab_credentials import (
    normalize_member_id,
    read_gitlab_credential_mappings,
)
from scripts import e2e_gitlab_member_workflow as runner

_SHA = runner._SHA
_git = runner._git
_run = runner._run
_secret_corpus = runner._secret_corpus
_repository_identity = runner._repository_identity
_multica_sync_command = runner._multica_sync_command
_member_observation = runner._member_observation


def _profile_status(args: argparse.Namespace) -> dict[str, object]:
    result = _run(
        [args.multica, "--profile", args.profile, "daemon", "status", "--output", "json"],
        env=_daemon_profile_environment(),
    )
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict):
        raise TypeError("isolated daemon status is invalid")
    return payload


def _profile_start(args: argparse.Namespace, environment: dict[str, str]) -> dict[str, object]:
    _run(
        [args.multica, "--profile", args.profile, "daemon", "start"],
        env=_daemon_profile_environment(environment),
    )
    status = _profile_status(args)
    if status.get("status") != "running" or status.get("profile") != args.profile:
        raise RuntimeError("isolated daemon did not become ready")
    return status


def _profile_stop(args: argparse.Namespace) -> None:
    _run(
        [args.multica, "--profile", args.profile, "daemon", "stop"],
        env=_daemon_profile_environment(),
    )


def _daemon_profile_environment(environment: dict[str, str] | None = None) -> dict[str, str]:
    selected = os.environ.copy() if environment is None else environment.copy()
    selected.pop("MULTICA_TOKEN", None)
    selected.pop("MULTICA_DAEMON_PORT", None)
    return selected


def _daemon_environment(status: dict[str, object]) -> dict[str, str]:
    daemon_pid = status.get("pid")
    if not isinstance(daemon_pid, int):
        raise TypeError("isolated daemon process identity is unavailable")
    try:
        import psutil

        return psutil.Process(daemon_pid).environ()
    except Exception as exc:
        raise RuntimeError("isolated daemon environment could not be verified") from exc


def _validate_integrated_cache_inputs(
    args: argparse.Namespace,
) -> tuple[str, str, str, tuple[Path, Path]]:
    required = (
        args.profile,
        args.helper_dir,
        args.core_project,
        args.sdk_sha,
        args.multica_project,
        args.workspace_id,
        args.cache_member_id,
        args.cache_owner_mismatch_member,
        args.cache_inactive_member,
        args.cache_broader_scope_member,
        args.cache_granular_scope_member,
        args.cache_incompatible_member,
        args.member_id,
        args.member_b,
        args.member_missing,
        args.member_invalid,
        args.member_nonhuman,
        args.member_cross_workspace,
        args.member_stale,
        args.cache_host,
        args.issue_a,
        args.run_a,
        args.issue_b,
        args.run_b,
        args.checkout_a,
        args.checkout_b,
        args.repository_a,
    )
    if any(value is None or value == "" for value in required):
        raise RuntimeError("multica-cache requires an isolated profile and complete test inputs")
    cache_member = normalize_member_id(args.cache_member_id)
    member_a = normalize_member_id(args.member_id)
    member_b = normalize_member_id(args.member_b)
    checkouts = (args.checkout_a.expanduser().resolve(), args.checkout_b.expanduser().resolve())
    if checkouts[0] == checkouts[1] or any(not path.is_dir() for path in checkouts):
        raise RuntimeError("two distinct existing task checkouts are required")
    parsed = urlsplit(args.repository_a)
    if (
        parsed.scheme.casefold() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.hostname
    ):
        raise RuntimeError("cache repository must be an HTTPS URL without embedded credentials")
    expected_host = parsed.hostname.casefold()
    if parsed.port is not None:
        expected_host = f"{expected_host}:{parsed.port}"
    if args.cache_host != expected_host:
        raise RuntimeError("cache host does not match the private repository authority")
    _validate_candidate_repository(args)
    return cache_member, member_a, member_b, checkouts


def _validate_candidate_repository(args: argparse.Namespace) -> tuple[Path, dict[str, str]]:
    project = args.core_project.expanduser().resolve(strict=True)
    root = Path(_git(args.git, project, "rev-parse", "--show-toplevel")).resolve(strict=True)
    if project != root:
        raise RuntimeError("core project must be the exact SDK repository root")
    if not _SHA.fullmatch(args.sdk_sha):
        raise RuntimeError("core project does not match the exact candidate SDK SHA")
    status = _git(
        args.git,
        root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--ignore-submodules=none",
    )
    if status:
        raise RuntimeError("candidate SDK repository must be clean before cache provenance checks")
    if _git(args.git, root, "rev-parse", "HEAD") != args.sdk_sha:
        raise RuntimeError("core project does not match the exact candidate SDK SHA")

    package_prefix = "src/odoo_instance_sdk/"
    tree = _git(args.git, root, "ls-tree", "-r", "-z", args.sdk_sha, "--", "src/odoo_instance_sdk")
    manifest: dict[str, str] = {}
    for record in tree.split("\0"):
        if not record:
            continue
        metadata, path = record.split("\t", 1)
        mode, object_type, object_id = metadata.split()
        if (
            object_type != "blob"
            or mode not in {"100644", "100755"}
            or not path.startswith(package_prefix)
        ):
            raise RuntimeError("candidate SDK Git package manifest contains an unsupported entry")
        manifest[path.removeprefix(package_prefix)] = object_id
    if not manifest:
        raise RuntimeError("candidate SDK Git package manifest is unavailable")
    return root, manifest


def _validate_integrated_checkouts(args: argparse.Namespace, checkouts: tuple[Path, Path]) -> None:
    for checkout in checkouts:
        if (checkout / ".odcli" / "gitlab-credentials.toml").exists() or (
            checkout / ".odcli" / ".env"
        ).exists():
            raise RuntimeError("task checkouts must not contain project credential copies")
        remote = _git(args.git, checkout, "remote", "get-url", "origin")
        if _repository_identity(remote) != _repository_identity(args.repository_a):
            raise RuntimeError("task checkout origin does not match the private test repository")
        if _git(args.git, checkout, "status", "--porcelain=v1", "--untracked-files=all"):
            raise RuntimeError("task checkouts must start clean")


def _integrated_cache_environments(
    args: argparse.Namespace,
    cache_member: str,
) -> tuple[dict[str, str], dict[str, str]]:
    cache_environment = os.environ.copy()
    cache_environment.pop("MULTICA_TOKEN", None)
    cache_environment.pop("MULTICA_DAEMON_PORT", None)
    cache_environment["PATH"] = (
        str(args.helper_dir.expanduser().resolve()) + os.pathsep + cache_environment.get("PATH", "")
    )
    cache_environment.update(
        {
            "ODCLI_GITLAB_PURPOSE": "cache-read",
            "ODCLI_GITLAB_MEMBER_ID": cache_member,
            "ODCLI_GITLAB_SELECTED_HOST": args.cache_host,
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "credential.helper",
            "GIT_CONFIG_VALUE_0": "",
            "GIT_CONFIG_KEY_1": "credential.helper",
            "GIT_CONFIG_VALUE_1": "odcli",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    prior_environment = os.environ.copy()
    for key in tuple(prior_environment):
        if key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")):
            prior_environment.pop(key, None)
    for key in (
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_TERMINAL_PROMPT",
        "ODCLI_GITLAB_PURPOSE",
        "ODCLI_GITLAB_MEMBER_ID",
        "ODCLI_GITLAB_SELECTED_HOST",
    ):
        prior_environment.pop(key, None)
    return cache_environment, prior_environment


@dataclass(frozen=True, slots=True)
class CacheRunState:
    cache_member: str
    member_a: str
    member_b: str
    checkouts: tuple[Path, Path]
    secret_values: tuple[str, ...] = field(repr=False)
    cache_environment: dict[str, str] = field(repr=False)
    prior_environment: dict[str, str] = field(repr=False)
    helper_path: Path = field(repr=False)


def _candidate_helper_path(helper_dir: Path) -> tuple[Path, Path]:
    requested_dir = helper_dir.expanduser().absolute()
    if requested_dir.is_symlink():
        raise RuntimeError("candidate SDK helper directory must not be a symlink")
    candidate_dir = requested_dir.resolve(strict=True)
    helper = shutil.which("git-credential-odcli", path=str(candidate_dir))
    if helper is None:
        raise RuntimeError("candidate SDK credential helper is unavailable")
    helper_path = Path(helper).resolve(strict=True)
    if not helper_path.is_relative_to(candidate_dir):
        raise RuntimeError("candidate SDK credential helper escapes its owned installation")
    install_root = candidate_dir.parent
    if candidate_dir.name not in {"bin", "Scripts"} or not install_root.is_dir():
        raise RuntimeError("candidate SDK helper directory is not an owned installation bin")
    return helper_path, install_root


def _helper_interpreter(helper_path: Path, install_root: Path) -> Path:
    try:
        launcher_lines = helper_path.read_text(encoding="utf-8").splitlines()[:2]
    except (OSError, UnicodeError) as exc:
        raise RuntimeError("candidate SDK credential helper launcher is invalid") from exc
    interpreter_match = next(
        (match for line in launcher_lines if (match := re.match(r"^'''exec' '([^']+)'", line))),
        None,
    )
    if interpreter_match is not None:
        interpreter_text = interpreter_match.group(1)
    else:
        shebang = next(
            (match for line in launcher_lines if (match := re.match(r"^#!\s*(\S+)", line))),
            None,
        )
        interpreter_text = None if shebang is None else shebang.group(1)
    if interpreter_text is None:
        raise RuntimeError("candidate SDK credential helper has no owned interpreter")
    interpreter = Path(interpreter_text).absolute()
    if not interpreter.parent.resolve().is_relative_to(install_root.resolve()):
        raise RuntimeError("candidate SDK credential helper uses a foreign interpreter")
    return interpreter


def _git_blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def _site_packages(install_root: Path) -> tuple[Path, ...]:
    candidates = [
        *install_root.glob("lib/python*/site-packages"),
        install_root / "Lib" / "site-packages",
    ]
    selected = tuple(
        sorted(
            {
                path.resolve(strict=True)
                for path in candidates
                if path.is_dir()
                and path.resolve(strict=True).is_relative_to(install_root.resolve())
            }
        )
    )
    if len(selected) != 1:
        raise RuntimeError("candidate SDK virtual environment has ambiguous site-packages")
    return selected


def _dist_info(site_packages: Path) -> Path:
    candidates = tuple(site_packages.glob("odoo_instance_sdk-*.dist-info"))
    if len(candidates) != 1 or candidates[0].is_symlink():
        raise RuntimeError("candidate SDK distribution metadata is unavailable or ambiguous")
    return candidates[0].resolve(strict=True)


def _record_rows(record: Path) -> tuple[list[str], ...]:
    try:
        return tuple(csv.reader(io.StringIO(record.read_text(encoding="utf-8"))))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise RuntimeError("candidate SDK installation RECORD is invalid") from exc


def _validate_record_row(
    row: list[str], dist_info: Path, install_root: Path, package_root: Path
) -> Path:
    if len(row) != 3:
        raise RuntimeError("candidate SDK installation RECORD is invalid")
    relative, digest, size = row
    unresolved = dist_info.parent / relative
    if unresolved.is_symlink():
        raise RuntimeError("candidate SDK installation RECORD references a symlink")
    path = unresolved.resolve(strict=True)
    roots = (install_root.resolve(), package_root.resolve())
    if not any(path.is_relative_to(root) for root in roots) or not path.is_file():
        raise RuntimeError("candidate SDK installation RECORD references an invalid file")
    content = path.read_bytes()
    if size and len(content) != int(size):
        raise RuntimeError("candidate SDK installation RECORD size does not match")
    if digest:
        algorithm, separator, encoded = digest.partition("=")
        actual = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).decode().rstrip("=")
        if not separator or algorithm != "sha256" or actual != encoded:
            raise RuntimeError("candidate SDK installation RECORD digest does not match")
    return path


def _validate_record(dist_info: Path, install_root: Path, package_root: Path) -> None:
    record = dist_info / "RECORD"
    seen = {
        _validate_record_row(row, dist_info, install_root, package_root)
        for row in _record_rows(record)
    }
    if record.resolve(strict=True) not in seen:
        raise RuntimeError("candidate SDK installation RECORD omits itself")


def _entry_points(path: Path) -> dict[str, str]:
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        parser.read_string(path.read_text(encoding="utf-8"))
        return dict(parser.items("console_scripts"))
    except (OSError, UnicodeError, configparser.Error) as exc:
        raise RuntimeError("candidate SDK entry-points metadata is invalid") from exc


def _expected_launcher(interpreter: Path) -> bytes:
    return (
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


def _package_root(site_packages: Path, source_root: Path, dist_info: Path) -> Path:
    direct_url = dist_info / "direct_url.json"
    if direct_url.exists():
        try:
            metadata = json.loads(direct_url.read_text(encoding="utf-8"))
            editable = metadata.get("dir_info", {}).get("editable") is True
            url = metadata.get("url")
            project_root = source_root.parent.parent
            expected_url = project_root.as_uri()
        except (OSError, UnicodeError, json.JSONDecodeError, AttributeError) as exc:
            raise RuntimeError("candidate SDK direct-url metadata is invalid") from exc
        if url != expected_url:
            raise RuntimeError("candidate SDK installation points to a different project")
        if editable:
            pth_files = tuple(site_packages.glob("*.pth"))
            expected_line = str(source_root.parent)
            if len(pth_files) != 1 or pth_files[0].name != "odoo_instance_sdk.pth":
                raise RuntimeError("candidate SDK editable path configuration is invalid")
            pth_lines = pth_files[0].read_text(encoding="utf-8").splitlines()
            if pth_lines != [expected_line]:
                raise RuntimeError("candidate SDK editable path configuration is unsafe")
            return source_root
    return (site_packages / "odoo_instance_sdk").resolve(strict=True)


def _candidate_distribution_metadata(
    site_packages: Path, install_root: Path, interpreter: Path, repository: Path
) -> tuple[Path, dict[str, object], Path]:
    dist_info = _dist_info(site_packages)
    pyvenv = install_root / "pyvenv.cfg"
    if pyvenv.is_symlink() or not pyvenv.is_file():
        raise RuntimeError("candidate SDK virtual environment metadata is invalid")
    try:
        config = {
            key.strip(): value.strip()
            for key, value in (
                line.split("=", 1)
                for line in pyvenv.read_text(encoding="utf-8").splitlines()
                if "=" in line
            )
        }
        home = Path(config["home"]).resolve(strict=True)
        distribution = (dist_info / "METADATA").read_text(encoding="utf-8").splitlines()
        project = tomllib.loads((repository / "pyproject.toml").read_text(encoding="utf-8"))[
            "project"
        ]
        version = next(
            line.partition(":")[2].strip() for line in distribution if line.startswith("Version:")
        )
        name = next(
            line.partition(":")[2].strip() for line in distribution if line.startswith("Name:")
        )
    except (OSError, UnicodeError, KeyError, StopIteration, tomllib.TOMLDecodeError) as exc:
        raise RuntimeError("candidate SDK virtual environment metadata is invalid") from exc
    if not interpreter.is_file() or not interpreter.parent.resolve().is_relative_to(
        install_root.resolve()
    ):
        raise RuntimeError("candidate SDK interpreter is outside its owned virtual environment")
    resolved_interpreter = interpreter.resolve(strict=True)
    if not resolved_interpreter.is_relative_to(home) and not resolved_interpreter.is_relative_to(
        install_root.resolve()
    ):
        raise RuntimeError("candidate SDK interpreter does not match its virtual environment")
    if name.casefold() != str(project["name"]).casefold() or version != str(project["version"]):
        raise RuntimeError(
            "candidate SDK distribution metadata does not match the candidate project"
        )

    expected_entries = dict(project.get("scripts", {}))
    entries = _entry_points(dist_info / "entry_points.txt")
    if entries != expected_entries or entries.get("git-credential-odcli") != (
        "odoo_instance_sdk.internal.git_credential_helper:main"
    ):
        raise RuntimeError("candidate SDK entry-points do not match the candidate project")
    return dist_info, project, home


def _validate_package_bytes(package_root: Path, manifest: dict[str, str]) -> None:
    if package_root.is_symlink() or not package_root.is_dir():
        raise RuntimeError("candidate SDK package root is invalid")
    actual_files: dict[str, str] = {}
    for directory, names, files in os.walk(package_root, followlinks=False):
        base = Path(directory)
        if any((base / name).is_symlink() for name in names):
            raise RuntimeError("candidate SDK package contains a directory symlink")
        names[:] = [name for name in names if name != "__pycache__"]
        for filename in files:
            path = base / filename
            if path.is_symlink():
                raise RuntimeError("candidate SDK package contains a file symlink")
            if filename.endswith(".pyc"):
                continue
            relative = str(path.relative_to(package_root))
            actual_files[relative] = _git_blob_id(path.read_bytes())
    if actual_files != manifest:
        raise RuntimeError("candidate SDK package bytes do not match the exact Git candidate")


def _validate_startup_hooks(site_packages: Path, package_root: Path, source_root: Path) -> None:
    site_customizations = (site_packages / "sitecustomize.py", site_packages / "usercustomize.py")
    if any(path.exists() for path in site_customizations):
        raise RuntimeError("candidate SDK virtual environment contains a startup customization")
    allowed_pth = tuple(site_packages.glob("*.pth"))
    editable = package_root.resolve() == source_root.resolve()
    if editable and (len(allowed_pth) != 1 or allowed_pth[0].name != "odoo_instance_sdk.pth"):
        raise RuntimeError("candidate SDK editable path configuration is invalid")
    if not editable and allowed_pth:
        raise RuntimeError("candidate SDK virtual environment contains an unverified path hook")


def _validate_candidate_package(
    install_root: Path,
    interpreter: Path,
    helper_path: Path,
    repository: Path,
    manifest: dict[str, str],
) -> None:
    source_root = repository / "src" / "odoo_instance_sdk"
    (site_packages,) = _site_packages(install_root)
    dist_info, _project, _home = _candidate_distribution_metadata(
        site_packages, install_root, interpreter, repository
    )
    package_root = _package_root(site_packages, source_root, dist_info)
    if package_root != source_root.resolve() and not package_root.is_relative_to(
        install_root.resolve()
    ):
        raise RuntimeError("candidate SDK package escapes its owned roots")
    _validate_package_bytes(package_root, manifest)
    _validate_startup_hooks(site_packages, package_root, source_root)
    _validate_record(dist_info, install_root, package_root)
    if helper_path.read_bytes() != _expected_launcher(interpreter):
        raise RuntimeError("candidate SDK helper launcher is not the expected entry point")


def _installed_helper_provenance(args: argparse.Namespace, environment: dict[str, str]) -> Path:
    helper_path, install_root = _candidate_helper_path(args.helper_dir)
    interpreter = _helper_interpreter(helper_path, install_root)
    repository, manifest = _validate_candidate_repository(args)
    _validate_candidate_package(install_root, interpreter, helper_path, repository, manifest)
    return helper_path


def _integrated_cache_readiness(
    args: argparse.Namespace,
    root: Path,
) -> CacheRunState:
    if not os.environ.get("MULTICA_DAEMON_PORT") or not os.environ.get("MULTICA_TOKEN"):
        raise RuntimeError("multica-cache requires an isolated daemon task context")
    if not root.resolve().is_relative_to(Path.cwd().resolve()):
        raise RuntimeError("multica-cache work root must be inside the isolated daemon task")
    cache_member, member_a, member_b, checkouts = _validate_integrated_cache_inputs(args)
    _validate_integrated_checkouts(args, checkouts)
    cache_secrets = _secret_corpus(cache_member)
    secret_values = tuple(
        dict.fromkeys(
            (
                *cache_secrets,
                *tuple(
                    secret
                    for member in (
                        args.cache_owner_mismatch_member,
                        args.cache_inactive_member,
                        args.cache_broader_scope_member,
                        args.cache_granular_scope_member,
                        args.cache_incompatible_member,
                    )
                    for secret in _secret_corpus(member)
                ),
                *_secret_corpus(member_a),
                *_secret_corpus(member_b),
            )
        )
    )
    cache_environment, prior_environment = _integrated_cache_environments(args, cache_member)
    helper_path = _installed_helper_provenance(args, cache_environment)
    return CacheRunState(
        cache_member=cache_member,
        member_a=member_a,
        member_b=member_b,
        checkouts=checkouts,
        secret_values=secret_values,
        cache_environment=cache_environment,
        prior_environment=prior_environment,
        helper_path=helper_path,
    )


def _validate_cache_helper(
    args: argparse.Namespace,
    cache_environment: dict[str, str],
    cache_member: str,
    secret_values: tuple[str, ...],
    *,
    expected_helper: Path | None = None,
) -> None:
    helper = _run_cache_helper(
        args,
        cache_environment,
        member_id=cache_member,
        request=f"protocol=https\nhost={args.cache_host}\n\n",
        expected_helper=expected_helper,
    )
    helper_fields = dict(
        line.partition("=")[::2] for line in helper.stdout.splitlines() if "=" in line
    )
    cache_mapping = next(
        (
            mapping
            for mapping in read_gitlab_credential_mappings()
            if mapping.user_id.casefold() == cache_member.casefold()
            and mapping.host == args.cache_host
        ),
        None,
    )
    if (
        helper.returncode != 0
        or cache_mapping is None
        or helper_fields.get("username") != cache_mapping.login
        or helper_fields.get("password") not in secret_values
    ):
        raise RuntimeError("installed cache helper rejected the configured account")


def _credential_helper_environment(
    args: argparse.Namespace,
    environment: dict[str, str],
    pycache_prefix: str,
    member_id: str,
    purpose: bool,
) -> dict[str, str]:
    allowed = (
        "HOME",
        "USERPROFILE",
        "XDG_CONFIG_HOME",
        "APPDATA",
        "TEMP",
        "TMP",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "CURL_CA_BUNDLE",
        "HTTPS_PROXY",
        "https_proxy",
        "HTTP_PROXY",
        "http_proxy",
        "NO_PROXY",
        "no_proxy",
        "ODCLI_GITLAB_USER_ROOT",
        "ODCLI_GITLAB_MAPPING_PATH",
        "ODCLI_GITLAB_ENVIRONMENT_PATH",
    )
    selected = {key: environment[key] for key in allowed if environment.get(key)}
    selected.update(
        {
            "PATH": os.pathsep.join((str(args.helper_dir.expanduser().resolve()), os.defpath)),
            "PYTHONNOUSERSITE": "1",
            "PYTHONPYCACHEPREFIX": pycache_prefix,
            "ODCLI_GITLAB_MEMBER_ID": normalize_member_id(member_id),
            "ODCLI_GITLAB_SELECTED_HOST": args.cache_host,
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "credential.helper",
            "GIT_CONFIG_VALUE_0": "",
            "GIT_CONFIG_KEY_1": "credential.helper",
            "GIT_CONFIG_VALUE_1": "odcli",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    if purpose:
        selected["ODCLI_GITLAB_PURPOSE"] = "cache-read"
    selected_members = {normalize_member_id(member_id).casefold()}
    token_keys = {
        mapping.token_key
        for mapping in read_gitlab_credential_mappings()
        if mapping.user_id.casefold() in selected_members
        and mapping.host.casefold() == args.cache_host.casefold()
    }
    selected.update((key, environment[key]) for key in token_keys if environment.get(key))
    return selected


def _run_cache_helper(
    args: argparse.Namespace,
    environment: dict[str, str],
    *,
    member_id: str,
    request: str,
    expected_helper: Path | None = None,
    purpose: bool = True,
) -> subprocess.CompletedProcess[str]:
    helper_path = _installed_helper_provenance(args, environment)
    if expected_helper is not None and helper_path != expected_helper:
        raise RuntimeError("candidate SDK credential helper changed after readiness")
    with tempfile.TemporaryDirectory(prefix="odcli-helper-pycache-") as pycache_prefix:
        helper_environment = _credential_helper_environment(
            args, environment, pycache_prefix, member_id, purpose
        )
        return subprocess.run(
            [args.git, "credential", "fill"],
            input=request,
            env=helper_environment,
            capture_output=True,
            text=True,
            check=False,
        )


def _stop_and_validate_cache_generation(
    args: argparse.Namespace,
    environment: dict[str, str],
    cache_member: str,
    secret_values: tuple[str, ...],
    *,
    stop_running_generation: bool,
    expected_helper: Path | None = None,
) -> None:
    if stop_running_generation:
        _profile_stop(args)
    _validate_cache_helper(
        args, environment, cache_member, secret_values, expected_helper=expected_helper
    )


def _assert_cache_helper_refusal(
    args: argparse.Namespace,
    environment: dict[str, str],
    secret_values: tuple[str, ...],
    *,
    host: str,
    request: str,
    member_id: str,
    expected_helper: Path,
    purpose: bool = True,
) -> None:
    attempt_environment = environment.copy()
    if not purpose:
        attempt_environment.pop("ODCLI_GITLAB_PURPOSE", None)
    attempt_environment["ODCLI_GITLAB_SELECTED_HOST"] = args.cache_host
    result = _run_cache_helper(
        args,
        attempt_environment,
        member_id=member_id,
        request=request.format(host=host),
        expected_helper=expected_helper,
        purpose=purpose,
    )
    if result.returncode == 0 or any(
        secret and secret in result.stdout for secret in secret_values
    ):
        raise RuntimeError("cache helper accepted an invalid request or exposed a secret")


def _assert_cache_helper_negative_inputs(
    args: argparse.Namespace,
    environment: dict[str, str],
    secret_values: tuple[str, ...],
    evidence: list[dict[str, str | int | bool]],
    expected_helper: Path,
) -> None:
    _assert_cache_helper_refusal(
        args,
        environment,
        secret_values,
        host="wrong.example",
        request="protocol=https\nhost={host}\n\n",
        member_id=args.cache_member_id,
        expected_helper=expected_helper,
    )
    _assert_cache_helper_refusal(
        args,
        environment,
        secret_values,
        host=args.cache_host,
        request="protocol=https\nhost={host}\nhost=wrong.example\n\n",
        member_id=args.cache_member_id,
        expected_helper=expected_helper,
    )
    _assert_cache_helper_refusal(
        args,
        environment,
        secret_values,
        host=args.cache_host,
        request="protocol=https\nhost={host}\n\n",
        member_id=args.cache_member_id,
        expected_helper=expected_helper,
        purpose=False,
    )
    evidence.append({"case": "C1", "negative_inputs": 3, "ok": True})

    for label, member in (
        ("owner_mismatch", args.cache_owner_mismatch_member),
        ("inactive_token", args.cache_inactive_member),
        ("broader_scope", args.cache_broader_scope_member),
        ("granular_scope", args.cache_granular_scope_member),
        ("incompatible_response", args.cache_incompatible_member),
    ):
        result = _run_cache_helper(
            args,
            environment,
            member_id=member,
            request=f"protocol=https\nhost={args.cache_host}\n\n",
            expected_helper=expected_helper,
        )
        response = result.stdout + result.stderr
        if result.returncode == 0 or any(secret and secret in response for secret in secret_values):
            raise RuntimeError(f"cache helper did not reject {label} fixture safely")
        evidence.append({"case": "C1", "rejected_fixture": label, "ok": True})


def _native_cache_checkout(
    args: argparse.Namespace,
    root: Path,
    cache_environment: dict[str, str],
    secret_values: tuple[str, ...],
    candidate_status: dict[str, object],
) -> None:
    task_environment = os.environ.copy()
    if not task_environment.get("MULTICA_DAEMON_PORT") or not task_environment.get("MULTICA_TOKEN"):
        raise RuntimeError("native checkout requires the isolated daemon task context")
    task_bindings = ("MULTICA_TASK_ID", "MULTICA_WORKSPACE_ID")
    if any(
        not task_environment.get(key) or cache_environment.get(key) != task_environment.get(key)
        for key in task_bindings
    ):
        raise RuntimeError("native checkout requires task and workspace bindings")
    wrapper = root / "native-cache-checkout"
    if not wrapper.resolve().is_relative_to(Path.cwd().resolve()):
        raise RuntimeError("native checkout workspace must be inside the isolated daemon task")
    expected_pid = _validate_native_cache_daemon(
        args, cache_environment, candidate_status, task_environment
    )
    wrapper.mkdir(mode=0o700, parents=True)
    result = _run(
        [args.multica, "repo", "checkout", args.repository_a],
        cwd=wrapper,
        env=task_environment,
        secrets=secret_values,
    )
    checkout = _native_checkout_result_path(result.stdout, wrapper)
    if Path(_git(args.git, checkout, "rev-parse", "--show-toplevel")).resolve() != checkout:
        raise RuntimeError("native Multica checkout response is not the actual worktree root")
    if _repository_identity(
        _git(args.git, checkout, "remote", "get-url", "origin")
    ) != _repository_identity(args.repository_a):
        raise RuntimeError("native Multica checkout origin does not match the private repository")
    final_status = _profile_status(args)
    if any(
        final_status.get(key) != expected
        for key, expected in (
            ("status", "running"),
            ("profile", args.profile),
            ("pid", expected_pid),
        )
    ):
        raise RuntimeError("native checkout candidate daemon generation changed during checkout")
    _assert_task_daemon_binding(args, task_environment, args.profile, expected_pid)


def _validate_native_cache_daemon(
    args: argparse.Namespace,
    cache_environment: dict[str, str],
    candidate_status: dict[str, object],
    task_environment: dict[str, str],
) -> int:
    expected_pid = candidate_status.get("pid")
    if (
        candidate_status.get("status") != "running"
        or candidate_status.get("profile") != args.profile
        or not isinstance(expected_pid, int)
    ):
        raise RuntimeError("native checkout candidate daemon identity is invalid")
    current_status = _profile_status(args)
    if any(
        current_status.get(key) != candidate_status.get(key) for key in ("status", "profile", "pid")
    ):
        raise RuntimeError("native checkout candidate daemon generation changed")
    _assert_task_daemon_binding(args, task_environment, args.profile, expected_pid)
    daemon_environment = _daemon_environment(candidate_status)
    task_bindings = ("MULTICA_TASK_ID", "MULTICA_WORKSPACE_ID")
    if any(daemon_environment.get(key) != cache_environment.get(key) for key in task_bindings):
        raise RuntimeError("native checkout candidate daemon task binding does not match")
    cache_bindings = (
        "ODCLI_GITLAB_PURPOSE",
        "ODCLI_GITLAB_MEMBER_ID",
        "ODCLI_GITLAB_SELECTED_HOST",
        "GIT_CONFIG_VALUE_1",
    )
    if any(daemon_environment.get(key) != cache_environment.get(key) for key in cache_bindings):
        raise RuntimeError("native checkout candidate daemon cache binding does not match")
    daemon_port = daemon_environment.get("MULTICA_DAEMON_PORT")
    if daemon_port is not None and daemon_port != task_environment["MULTICA_DAEMON_PORT"]:
        raise RuntimeError(
            "native checkout task transport port does not match the candidate daemon"
        )
    return expected_pid


def _assert_task_daemon_binding(
    args: argparse.Namespace,
    task_environment: dict[str, str],
    profile: str,
    expected_pid: int,
) -> None:
    result = _run(
        [args.multica, "daemon", "status", "--output", "json"],
        env=task_environment,
    )
    try:
        status = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("task daemon status response is invalid") from exc
    if not isinstance(status, dict) or any(
        status.get(key) != expected
        for key, expected in (("status", "running"), ("profile", profile), ("pid", expected_pid))
    ):
        raise RuntimeError("native checkout task transport is not bound to the candidate daemon")


def _native_checkout_result_path(stdout: str, wrapper: Path) -> Path:
    reported_path = stdout.strip()
    if not reported_path or "\n" in reported_path or not Path(reported_path).is_absolute():
        raise RuntimeError("native Multica checkout did not return one absolute worktree path")
    checkout = Path(reported_path).resolve()
    owned_root = wrapper.resolve()
    if checkout == owned_root or not checkout.is_relative_to(owned_root):
        raise RuntimeError("native Multica checkout path is outside the owned checkout root")
    if not checkout.is_dir():
        raise RuntimeError("native Multica checkout path is not an existing directory")
    return checkout


def _run_integrated_member_operations(
    args: argparse.Namespace,
    checkouts: tuple[Path, Path],
    member_a: str,
    member_b: str,
    prior_environment: dict[str, str],
    secret_values: tuple[str, ...],
    evidence: list[dict[str, str | int | bool]],
) -> None:
    for case, checkout, issue, run_id, selected, expected_id in (
        ("C2", checkouts[0], args.issue_a, args.run_a, None, member_a),
        ("C3", checkouts[1], args.issue_b, args.run_b, member_b, member_b),
    ):
        preview = _run(
            _multica_sync_command(
                args,
                checkout=checkout,
                issue=issue,
                run_id=run_id,
                member_id=selected,
                dry_run=True,
            ),
            env=prior_environment,
            secrets=secret_values,
        )
        observation = _member_observation(json.loads(preview.stdout))
        expected_source = "root_creator" if selected is None else "explicit"
        if observation != (expected_id, expected_source):
            raise RuntimeError(f"{case} selected-member observation is incorrect")
        _run(
            _multica_sync_command(
                args,
                checkout=checkout,
                issue=issue,
                run_id=run_id,
                member_id=selected,
                dry_run=False,
            ),
            env=prior_environment,
            secrets=secret_values,
        )
        evidence.append({"case": case, "sync": True, "ok": True})

    failed = subprocess.run(
        _multica_sync_command(
            args,
            checkout=checkouts[1],
            issue=args.issue_b,
            run_id=args.run_b,
            member_id=normalize_member_id(args.member_missing),
            dry_run=False,
        ),
        env=prior_environment,
        capture_output=True,
        text=True,
        check=False,
    )
    failure_text = failed.stdout + failed.stderr
    if failed.returncode == 0 or any(secret and secret in failure_text for secret in secret_values):
        raise RuntimeError("missing selected credentials did not fail safely")
    evidence.append({"case": "C3", "missing_member_failed": True, "ok": True})

    for label, invalid_member in (
        ("invalid", args.member_invalid),
        ("nonhuman", args.member_nonhuman),
        ("cross_workspace", args.member_cross_workspace),
        ("stale", args.member_stale),
    ):
        _assert_public_member_refusal(
            args,
            checkout=checkouts[1],
            member_id=invalid_member,
            secret_values=secret_values,
            prior_environment=prior_environment,
        )
        evidence.append({"case": "C2", "rejected_member": label, "ok": True})


def _assert_public_member_refusal(
    args: argparse.Namespace,
    *,
    checkout: Path,
    member_id: str,
    secret_values: tuple[str, ...],
    prior_environment: dict[str, str],
) -> None:
    rejected = subprocess.run(
        _multica_sync_command(
            args,
            checkout=checkout,
            issue=args.issue_b,
            run_id=args.run_b,
            member_id=member_id,
            dry_run=True,
        ),
        env=prior_environment,
        capture_output=True,
        text=True,
        check=False,
    )
    rejection = rejected.stdout + rejected.stderr
    if rejected.returncode == 0 or any(secret and secret in rejection for secret in secret_values):
        raise RuntimeError("selected-member rejection was not fail-closed and secret-safe")


def _rollback_cache_generation(
    args: argparse.Namespace,
    prior_environment: dict[str, str],
    cache_environment: dict[str, str],
) -> None:
    _profile_stop(args)
    status = _profile_start(args, prior_environment)
    inherited = _daemon_environment(status)
    replacement = (
        "ODCLI_GITLAB_PURPOSE",
        "ODCLI_GITLAB_MEMBER_ID",
        "ODCLI_GITLAB_SELECTED_HOST",
        "GIT_CONFIG_VALUE_1",
    )
    if any(inherited.get(key) == cache_environment.get(key) for key in replacement):
        raise RuntimeError("rollback daemon inherited replacement cache settings")


def integrated_multica_cache(
    args: argparse.Namespace,
    root: Path,
    evidence: list[dict[str, str | int | bool]],
) -> tuple[str, ...]:
    state = _integrated_cache_readiness(args, root)
    cache_member = state.cache_member
    member_a = state.member_a
    member_b = state.member_b
    checkouts = state.checkouts
    secret_values = state.secret_values
    cache_environment = state.cache_environment
    prior_environment = state.prior_environment
    initial_status = _profile_status(args)
    if initial_status.get("profile") != args.profile:
        raise RuntimeError("Multica returned a different profile")

    candidate_started = False
    rollback_started = False
    try:
        _stop_and_validate_cache_generation(
            args,
            cache_environment,
            cache_member,
            secret_values,
            stop_running_generation=initial_status.get("status") == "running",
            expected_helper=state.helper_path,
        )
        evidence.append({"case": "C1", "helper_exit": 0, "ok": True})
        _assert_cache_helper_negative_inputs(
            args, cache_environment, secret_values, evidence, state.helper_path
        )

        candidate_started = True
        candidate_status = _profile_start(args, cache_environment)
        candidate_process_environment = _daemon_environment(candidate_status)
        if any(
            candidate_process_environment.get(key) != cache_environment.get(key)
            for key in (
                "ODCLI_GITLAB_PURPOSE",
                "ODCLI_GITLAB_MEMBER_ID",
                "ODCLI_GITLAB_SELECTED_HOST",
                "GIT_CONFIG_VALUE_1",
            )
        ):
            raise RuntimeError("candidate daemon did not inherit cache-read settings")
        _native_cache_checkout(args, root, cache_environment, secret_values, candidate_status)
        evidence.append({"case": "C1", "native_cache_checkout": True, "ok": True})

        _run_integrated_member_operations(
            args, checkouts, member_a, member_b, prior_environment, secret_values, evidence
        )

        _profile_stop(args)
        candidate_started = False
        rollback_started = True
        _rollback_cache_generation(args, prior_environment, cache_environment)
        evidence.append({"case": "C4", "rollback_started": True, "ok": True})
    finally:
        if candidate_started or rollback_started:
            _profile_stop(args)
    return secret_values
