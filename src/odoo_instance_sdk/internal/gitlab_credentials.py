"""Global, owner-only GitLab credential and askpass policies."""

from __future__ import annotations

import contextlib
import errno
import json
import os
import re
import stat
import sys
import tempfile
import tomllib
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import cast
from urllib.parse import urlsplit

from odoo_instance_sdk.exceptions import GitLabCredentialError
from odoo_instance_sdk.execution import JsonValue
from odoo_instance_sdk.internal.paths import get_user_root
from odoo_instance_sdk.internal.proc import PreparedStep, ProcessResult, SubprocessExecutor
from odoo_instance_sdk.models import (
    GitLabCredentialContext,
    GitLabCredentialIdentity,
    GitLabCredentialMapping,
)

_TOKEN_KEY = re.compile(r"ODCLI_GITLAB_TOKEN_[A-Za-z0-9_]+\Z")
_OWNER_ONLY = 0o077


def normalize_gitlab_host(value: str) -> str:
    """Return the exact lower-case HTTPS authority used for credential lookup."""
    raw = value.strip()
    parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.netloc
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise GitLabCredentialError("GitLab host must be an exact HTTPS authority")
    try:
        hostname, port = parsed.hostname, parsed.port
    except ValueError as exc:
        raise GitLabCredentialError("GitLab host must be an exact HTTPS authority") from exc
    if hostname is None or not hostname.strip() or "*" in hostname:
        raise GitLabCredentialError("GitLab host must be an exact HTTPS authority")
    return f"{hostname.casefold()}:{port}" if port is not None else hostname.casefold()


gitlab_host = normalize_gitlab_host


def credential_directory(user_root: Path | str | None = None) -> Path:
    root = get_user_root(ensure_exists=False) if user_root is None else Path(user_root)
    return root.expanduser().resolve() / "credentials"


def credential_mapping_path(user_root: Path | str | None = None) -> Path:
    return credential_directory(user_root) / "gitlab-credentials.toml"


def credential_environment_path(user_root: Path | str | None = None) -> Path:
    return credential_directory(user_root) / ".env"


def ensure_credential_directory(user_root: Path | str | None = None) -> Path:
    directory = credential_directory(user_root)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    return directory


def _owner_only_open_flags(*, directory: bool = False) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if nofollow == 0 or not hasattr(os, "getuid"):
        raise GitLabCredentialError("owner-only credential reads are unavailable")
    flags = os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0)
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    return flags


def _validate_owner_only_identity(metadata: os.stat_result, *, path: Path, label: str) -> None:
    if metadata.st_uid != os.getuid():
        raise GitLabCredentialError(f"{label} must be owned by the current user: {path}")
    if stat.S_IMODE(metadata.st_mode) & _OWNER_ONLY:
        raise GitLabCredentialError(f"{label} requires owner-only permissions: {path}")


def _owner_only_file(path: Path, *, label: str) -> str:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, _owner_only_open_flags())
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise GitLabCredentialError(f"{label} must be a regular owner-only file: {path}")
        _validate_owner_only_identity(metadata, path=path, label=label)
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            descriptor = None
            return stream.read()
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise GitLabCredentialError(
                f"{label} must be a regular owner-only file: {path}"
            ) from exc
        raise GitLabCredentialError(f"{label} is unavailable: {path}") from exc
    except UnicodeError as exc:
        raise GitLabCredentialError(f"{label} is unavailable: {path}") from exc
    finally:
        if descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(descriptor)


def _owner_only_directory(path: Path) -> None:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, _owner_only_open_flags(directory=True))
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise GitLabCredentialError(f"credential directory must be owner-only: {path}")
        _validate_owner_only_identity(metadata, path=path, label="credential directory")
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise GitLabCredentialError(f"credential directory must be owner-only: {path}") from exc
        raise GitLabCredentialError(f"credential directory is unavailable: {path}") from exc
    finally:
        if descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(descriptor)


def _text(entry: Mapping[str, JsonValue], key: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip() or any(char in value for char in "\r\n"):
        raise GitLabCredentialError("GitLab credential mapping is malformed")
    return value.strip()


def _named_entries(raw: Mapping[str, JsonValue]) -> tuple[JsonValue, ...] | None:
    for key in ("mappings", "credentials", "credential", "gitlab"):
        named = raw.get(key)
        if isinstance(named, list):
            return tuple(named)
        if isinstance(named, Mapping):
            return (named,)
    return None


def _user_entries(users: Mapping[str, JsonValue]) -> tuple[JsonValue, ...]:
    flattened: list[Mapping[str, JsonValue]] = []
    for user_id, hosts in users.items():
        if not isinstance(hosts, Mapping):
            raise GitLabCredentialError("GitLab credential mapping is malformed")
        for host, details in hosts.items():
            if not isinstance(details, Mapping):
                raise GitLabCredentialError("GitLab credential mapping is malformed")
            flattened.append({"user_id": user_id, "host": host, **details})
    return tuple(cast("JsonValue", item) for item in flattened)


def _entries(raw: JsonValue) -> tuple[Mapping[str, JsonValue], ...]:
    values: tuple[JsonValue, ...]
    if isinstance(raw, list):
        values = tuple(raw)
    else:
        if not isinstance(raw, Mapping):
            raise GitLabCredentialError("GitLab credential mapping is malformed")
        named = _named_entries(raw)
        if named is not None:
            values = named
        else:
            users = raw.get("users")
            if isinstance(users, Mapping):
                values = _user_entries(users)
            elif {"user_id", "host", "login", "token_key"}.issubset(raw):
                values = (raw,)
            else:
                raise GitLabCredentialError("GitLab credential mapping is malformed")
    if not all(isinstance(value, Mapping) for value in values):
        raise GitLabCredentialError("GitLab credential mapping is malformed")
    return tuple(
        cast("Mapping[str, JsonValue]", value) for value in values if isinstance(value, Mapping)
    )


def read_gitlab_credential_mappings(
    path: Path | str | None = None,
    *,
    user_root: Path | str | None = None,
    require_owner_only_directory: bool = True,
    validate_member_ids: bool = True,
) -> tuple[GitLabCredentialMapping, ...]:
    """Read and validate the non-secret global mapping file."""
    mapping_path = credential_mapping_path(user_root) if path is None else Path(path)
    if require_owner_only_directory:
        _owner_only_directory(mapping_path.parent)
    text = _owner_only_file(mapping_path, label="GitLab credential mapping")
    try:
        raw = cast(
            "JsonValue",
            json.loads(text) if mapping_path.suffix.casefold() == ".json" else tomllib.loads(text),
        )
    except (tomllib.TOMLDecodeError, ValueError) as exc:
        raise GitLabCredentialError("GitLab credential mapping is malformed") from exc
    result: list[GitLabCredentialMapping] = []
    seen: set[tuple[str, str]] = set()
    for entry in _entries(raw):
        raw_user_id = _text(entry, "user_id")
        user_id = normalize_member_id(raw_user_id) if validate_member_ids else raw_user_id
        host = normalize_gitlab_host(_text(entry, "host"))
        login = _text(entry, "login")
        token_key = _text(entry, "token_key")
        if not _TOKEN_KEY.fullmatch(token_key):
            raise GitLabCredentialError("GitLab credential token key is malformed")
        key = (user_id, host)
        if key in seen:
            raise GitLabCredentialError("GitLab credential mapping is ambiguous")
        seen.add(key)
        result.append(
            GitLabCredentialMapping(user_id=user_id, host=host, login=login, token_key=token_key)
        )
    return tuple(result)


def _dotenv(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key.strip()):
            raise GitLabCredentialError(
                f"GitLab credential environment is malformed at line {number}"
            )
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def read_gitlab_environment(
    path: Path | str | None = None,
    *,
    user_root: Path | str | None = None,
) -> Mapping[str, str]:
    env_path = credential_environment_path(user_root) if path is None else Path(path)
    _owner_only_directory(env_path.parent)
    return MappingProxyType(
        _dotenv(_owner_only_file(env_path, label="GitLab credential environment"))
    )


def normalize_member_id(value: str) -> str:
    try:
        return str(uuid.UUID(value.strip()))
    except (AttributeError, ValueError) as exc:
        raise GitLabCredentialError("member ID must be a UUID") from exc


def resolve_gitlab_credential(
    member_id: str,
    host: str,
    *,
    user_root: Path | str | None = None,
    mapping_path: Path | str | None = None,
    environment_path: Path | str | None = None,
    process_environment: Mapping[str, str] | None = None,
) -> GitLabCredentialContext:
    """Resolve one exact member/host pair without consulting Git credentials."""
    selected_member = normalize_member_id(member_id)
    normalized_host = normalize_gitlab_host(host)
    mappings = read_gitlab_credential_mappings(mapping_path, user_root=user_root)
    matches = tuple(
        item
        for item in mappings
        if item.user_id.casefold() == selected_member.casefold() and item.host == normalized_host
    )
    if len(matches) != 1:
        reason = "unavailable" if not matches else "ambiguous"
        raise GitLabCredentialError(f"GitLab credential mapping is {reason}")
    mapping = matches[0]
    file_values = read_gitlab_environment(environment_path, user_root=user_root)
    source = os.environ if process_environment is None else process_environment
    token = (
        source[mapping.token_key]
        if mapping.token_key in source
        else file_values.get(mapping.token_key, "")
    )
    if not token:
        raise GitLabCredentialError(
            f"GitLab credential token key {mapping.token_key} is unavailable"
        )
    return GitLabCredentialContext(
        identity=GitLabCredentialIdentity(
            user_id=selected_member,
            host=mapping.host,
            login=mapping.login,
            token_key=mapping.token_key,
        ),
        _token=token,
    )


def write_askpass_channel(
    credential: GitLabCredentialContext | Sequence[GitLabCredentialContext],
) -> str:
    """Create a short-lived owner-only JSON channel for one Git child."""
    credentials = (
        (credential,) if isinstance(credential, GitLabCredentialContext) else tuple(credential)
    )
    if not credentials:
        raise GitLabCredentialError("Git askpass requires at least one credential")
    fd, path = tempfile.mkstemp(prefix="odcli-gitlab-", suffix=".askpass")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as channel:
            json.dump(
                [
                    {"host": item.host, "login": item.login, "token": item._token}
                    for item in credentials
                ],
                channel,
                separators=(",", ":"),
            )
            channel.write("\n")
        os.chmod(path, 0o600)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(path)
        raise
    return path


def packaged_askpass() -> str:
    """Return the one-file packaged helper executable for Git."""
    import sys

    helper_name = "odcli-git-askpass.exe" if os.name == "nt" else "odcli-git-askpass"
    helper = Path(sys.executable).with_name(helper_name)
    ensure_packaged_askpass(helper)
    if not helper.is_file() or not os.access(helper, os.X_OK):
        raise GitLabCredentialError("GitLab askpass helper is unavailable")
    return str(helper)


def ensure_packaged_askpass(helper: Path) -> None:
    """Atomically publish the exact current-interpreter launcher."""
    interpreter = Path(sys.executable).absolute()
    if not helper.parent.is_dir():
        return
    content = (
        f"#!{interpreter}\n"
        "from odoo_instance_sdk.internal.git_askpass import main\n"
        "raise SystemExit(main())\n"
    ).encode()
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{helper.name}.", dir=helper.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o700)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(content)
        os.replace(temporary, helper)
    except BaseException:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise
    finally:
        if descriptor >= 0:
            with contextlib.suppress(OSError):
                os.close(descriptor)


def cleanup_askpass_channel(path: str | None) -> None:
    if path:
        with contextlib.suppress(OSError):
            os.unlink(path)


def https_host(value: str) -> str | None:
    """Return a normalized HTTPS authority, or ``None`` for non-HTTPS input."""
    parsed = urlsplit(value.strip() if "://" in value else f"https://{value.strip()}")
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    try:
        hostname, port = parsed.hostname, parsed.port
    except ValueError:
        return None
    if hostname is None or not hostname.strip() or "*" in hostname:
        return None
    return f"{hostname.casefold()}:{port}" if port is not None else hostname.casefold()


def _transport_probe(root: Path, args: Sequence[str], step_id: str) -> ProcessResult:
    step = PreparedStep(
        step_id=step_id,
        argv=("git", "-C", str(root), *tuple(args)),
        cwd=str(root),
        environment=(
            ("GIT_CONFIG_GLOBAL", os.devnull),
            ("GIT_CONFIG_NOSYSTEM", "1"),
            ("GIT_CONFIG_SYSTEM", os.devnull),
        ),
        environment_policy="explicit",
        read_only=True,
        text=True,
        timeout=15.0,
    )
    try:
        result = SubprocessExecutor().execute(step)
    except Exception as exc:
        raise GitLabCredentialError("Git transport preflight is unavailable") from exc
    if not isinstance(result, ProcessResult):
        raise GitLabCredentialError("Git transport preflight is unavailable")
    return result


def _validate_transport_args(args: Sequence[str], expected_host: str) -> None:
    dangerous = (
        "-C",
        "-c",
        "--config",
        "--config-env",
        "--exec-path",
        "--git-dir",
        "--work-tree",
        "--upload-pack",
        "--receive-pack",
    )
    if any(
        argument == option or argument.startswith(f"{option}=")
        for argument in args
        for option in dangerous
    ):
        raise GitLabCredentialError("credentialed Git rejects command overrides")
    direct_hosts = tuple(host for argument in args if (host := https_host(argument)) is not None)
    if any(host != expected_host for host in direct_hosts):
        raise GitLabCredentialError("Git transport host is ambiguous")


def _validate_submodule_hosts(root: Path, expected_host: str) -> None:
    helper = _transport_probe(
        root, ("config", "--local", "--get-all", "credential.helper"), "git.transport.helper"
    )
    helper_output = helper.stdout if isinstance(helper.stdout, str) else ""
    if helper.returncode not in (0, 1) or helper_output.strip():
        raise GitLabCredentialError("repository credential helper is configured")
    modules = root / ".gitmodules"
    if modules.is_symlink():
        raise GitLabCredentialError("submodule configuration is not repository-owned")
    if not modules.is_file():
        return
    urls = _transport_probe(
        root,
        ("config", "--file", str(modules), "--get-regexp", r"^submodule\..*\.url$"),
        "git.transport.submodule-urls",
    )
    if urls.returncode not in (0, 1) or not isinstance(urls.stdout, str):
        raise GitLabCredentialError("submodule transport evidence is unavailable")
    for line in urls.stdout.splitlines():
        _, _, value = line.partition(" ")
        submodule_host = https_host(value)
        if submodule_host is not None and submodule_host != expected_host:
            raise GitLabCredentialError("foreign submodule transport authority")


def verify_git_transport(
    checkout: Path | str,
    args: Sequence[str],
    repository_url: str,
) -> None:
    """Reject credential-routing overrides and foreign HTTPS submodule hosts."""
    expected_host = https_host(repository_url)
    if expected_host is None:
        return
    _validate_transport_args(args, expected_host)
    root = Path(checkout).expanduser().resolve()
    if root.is_dir():
        _validate_submodule_hosts(root, expected_host)


__all__ = [
    "cleanup_askpass_channel",
    "credential_directory",
    "credential_environment_path",
    "credential_mapping_path",
    "ensure_credential_directory",
    "gitlab_host",
    "https_host",
    "normalize_gitlab_host",
    "normalize_member_id",
    "packaged_askpass",
    "read_gitlab_credential_mappings",
    "read_gitlab_environment",
    "resolve_gitlab_credential",
    "verify_git_transport",
    "write_askpass_channel",
]
