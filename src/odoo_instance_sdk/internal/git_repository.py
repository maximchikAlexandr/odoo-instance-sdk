"""Credential-free generic Git repository resolution and revalidation."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlsplit

from odoo_instance_sdk.exceptions import GitRepositoryChangedError, GitRepositoryError
from odoo_instance_sdk.internal.proc import PreparedStep, ProcessResult, SubprocessExecutor
from odoo_instance_sdk.models import GitRepositorySnapshot

_PROJECT_PART = re.compile(r"^[^/\\?#]+$")


def _probe(root: Path, args: Sequence[str], step_id: str) -> ProcessResult:
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
        raise GitRepositoryError(f"Git repository evidence is unavailable: {step_id}") from exc
    if not isinstance(result, ProcessResult):
        raise GitRepositoryError(f"Git repository evidence is unavailable: {step_id}")
    return result


def _stdout(result: ProcessResult, label: str) -> str:
    if result.returncode != 0 or not isinstance(result.stdout, str):
        raise GitRepositoryError(f"Git repository {label} is unavailable")
    return result.stdout


def _normalize_origin(value: str) -> tuple[str, str, str]:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme.casefold() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.hostname
        or parsed.query
        or parsed.fragment
        or not parsed.path.strip("/")
    ):
        raise GitRepositoryError("Git repository origin must be an absolute HTTPS GitLab URL")
    try:
        port = parsed.port
    except ValueError as exc:
        raise GitRepositoryError("Git repository origin is invalid") from exc
    host = parsed.hostname.casefold()
    authority = f"{host}:{port}" if port is not None else host
    project = parsed.path.strip("/")
    if project.endswith(".git"):
        project = project[:-4]
    parts = project.split("/")
    if not project or any(
        not _PROJECT_PART.fullmatch(part) or part in {".", ".."} for part in parts
    ):
        raise GitRepositoryError("Git repository project path is invalid")
    return f"https://{authority}/{project}", authority, project


def _resolve_submodule_url(origin: str, value: str) -> str:
    """Resolve a committed relative URL using Git's superproject directory."""
    value = value.strip()
    scp = re.fullmatch(r"git@([^/:\s]+):(.+)", value)
    if scp is not None:
        if scp[2].startswith(("/", "~")):
            raise GitRepositoryError(
                "submodule SSH URL must use a repository-relative project path"
            )
        return f"https://{scp[1]}/{quote(scp[2], safe='/')}"
    parsed = urlsplit(value)
    if parsed.scheme.casefold() == "ssh":
        try:
            port = parsed.port
            path = unquote(parsed.path, errors="strict")
        except (ValueError, UnicodeError) as exc:
            raise GitRepositoryError("submodule SSH URL cannot be mapped to HTTPS") from exc
        if (
            parsed.username != "git"
            or parsed.password is not None
            or port not in (None, 22)
            or not parsed.hostname
            or path.startswith(("//", "/~"))
            or parsed.query
            or parsed.fragment
        ):
            raise GitRepositoryError("submodule SSH URL cannot be mapped to HTTPS")
        return f"https://{parsed.hostname}{quote(path, safe='/')}"
    if ":" in value and not parsed.scheme:
        raise GitRepositoryError("submodule SSH URL cannot be mapped to HTTPS")
    return urljoin(f"{origin.rstrip('/')}/", value)


def normalize_repository_url(value: str) -> tuple[str, str, str]:
    """Validate and normalize one credential-free GitLab HTTPS repository URL."""
    return _normalize_origin(value)


def repository_project_path(origin: str) -> tuple[str, str]:
    """Return normalized host/project identity from an HTTPS GitLab URL."""
    _canonical, host, project = _normalize_origin(origin)
    return host, project


def _staged_paths(root: Path) -> tuple[str, ...]:
    result = _probe(root, ("diff", "--cached", "--name-only", "-z"), "git.repository.staged")
    value = _stdout(result, "staged state")
    return tuple(path for path in value.split("\0") if path)


def _index_tree(root: Path) -> str:
    result = _probe(root, ("write-tree",), "git.repository.index-tree")
    return _stdout(result, "index tree").strip()


def _common_dir(root: Path) -> str:
    value = _stdout(
        _probe(root, ("rev-parse", "--git-common-dir"), "git.repository.common-dir"),
        "common directory",
    ).strip()
    path = Path(value)
    return str((root / path if not path.is_absolute() else path).resolve())


def resolve_repository(path: Path | str) -> GitRepositorySnapshot:
    """Capture canonical root, origin, branch, staged state, and Odoo class."""
    requested = Path(path).expanduser().resolve()
    if not requested.exists() or not requested.is_dir():
        raise GitRepositoryError("Git repository root is unavailable")
    root = Path(
        _stdout(
            _probe(requested, ("rev-parse", "--show-toplevel"), "git.repository.root"), "root"
        ).strip()
    ).resolve()
    origin_result = _probe(root, ("remote", "get-url", "--all", "origin"), "git.repository.origin")
    origins = tuple(
        line.strip() for line in _stdout(origin_result, "origin").splitlines() if line.strip()
    )
    if len(origins) != 1:
        raise GitRepositoryError("Git repository origin is absent or ambiguous")
    origin, host, project = _normalize_origin(origins[0])
    branch_result = _probe(
        root, ("symbolic-ref", "--quiet", "--short", "HEAD"), "git.repository.branch"
    )
    branch = (
        branch_result.stdout.strip()
        if branch_result.returncode == 0 and isinstance(branch_result.stdout, str)
        else None
    )
    status = _probe(root, ("status", "--porcelain=v1"), "git.repository.status")
    dirty = bool(_stdout(status, "worktree state").strip())
    staged = _staged_paths(root)
    return GitRepositorySnapshot(
        canonical_root=str(root),
        git_common_dir=_common_dir(root),
        origin=origin,
        host=host,
        project=project,
        branch=branch or None,
        staged_paths=staged,
        worktree_dirty=dirty,
        initialized_odoo=(root / ".odcli" / "project.toml").is_file(),
        index_tree=_index_tree(root),
    )


resolve_generic_repository = resolve_repository


def revalidate_repository(
    snapshot: GitRepositorySnapshot, *, expected_push_origin: str | None = None
) -> GitRepositorySnapshot:
    current = resolve_repository(snapshot.canonical_root)
    if current != snapshot:
        raise GitRepositoryChangedError("repository evidence changed after planning")
    if (
        expected_push_origin is not None
        and repository_push_origin(snapshot.canonical_root) != expected_push_origin
    ):
        raise GitRepositoryChangedError("Git push origin changed after sync planning")
    return current


def repository_push_origin(root: Path | str) -> str:
    checkout = Path(root).expanduser().resolve()
    result = _probe(
        checkout,
        ("remote", "get-url", "--push", "--all", "origin"),
        "git.repository.push-origin",
    )
    if result.returncode != 0 or not isinstance(result.stdout, str):
        raise GitRepositoryError("Git push origin is unavailable")
    values = tuple(line.strip() for line in result.stdout.splitlines() if line.strip())
    if len(values) != 1:
        raise GitRepositoryError("Git push origin is absent or ambiguous")
    return _normalize_origin(values[0])[0]


def _configured_values(root: Path, key_pattern: str, step_id: str) -> tuple[str, ...]:
    result = _probe(root, ("config", "--local", "--get-regexp", key_pattern), step_id)
    if result.returncode not in (0, 1) or not isinstance(result.stdout, str):
        raise GitRepositoryError("repository Git configuration is unavailable")
    return tuple(line.strip() for line in result.stdout.splitlines() if line.strip())


def _submodule_entries(root: Path | str) -> tuple[tuple[str, str, str, str], ...]:  # noqa: C901
    """Return ``(name, path, normalized URL, host)`` for declared submodules.

    This reads only committed ``.gitmodules`` data and never asks Git to resolve
    or fetch a credentialed URL. Relative URLs are resolved against the
    superproject origin so host policy is applied before a child is started.
    """
    checkout = Path(root).expanduser().resolve()
    modules = checkout / ".gitmodules"
    if modules.is_symlink():
        raise GitRepositoryError("submodule configuration is not repository-owned")
    if not modules.is_file():
        return ()
    origin, _host, _project = _normalize_origin(
        _resolve_submodule_url(
            "",
            _stdout(
                _probe(checkout, ("remote", "get-url", "origin"), "git.submodule.origin"), "origin"
            ).strip(),
        )
    )
    url_result = _probe(
        checkout,
        (
            "config",
            "--file",
            str(modules),
            "--get-regexp",
            r"^submodule\..*\.url$",
        ),
        "git.submodule.urls",
    )
    if url_result.returncode not in (0, 1) or not isinstance(url_result.stdout, str):
        raise GitRepositoryError("repository submodule URL configuration is unavailable")
    values = tuple(line.strip() for line in url_result.stdout.splitlines() if line.strip())
    path_result = _probe(
        checkout,
        (
            "config",
            "--file",
            str(modules),
            "--get-regexp",
            r"^submodule\..*\.path$",
        ),
        "git.submodule.paths",
    )
    if path_result.returncode not in (0, 1) or not isinstance(path_result.stdout, str):
        raise GitRepositoryError("repository submodule path configuration is unavailable")
    paths = tuple(line.strip() for line in path_result.stdout.splitlines() if line.strip())
    path_by_name: dict[str, str] = {}
    for line in paths:
        key, separator, raw_path = line.partition(" ")
        name = key.removeprefix("submodule.").removesuffix(".path")
        if not separator or not name or name in path_by_name:
            raise GitRepositoryError("submodule path configuration is malformed")
        name_path = Path(name)
        if (
            "=" in name
            or name_path.is_absolute()
            or name_path == Path(".")
            or ".." in name_path.parts
        ):
            raise GitRepositoryError("submodule logical name is unsafe")
        path_by_name[name] = raw_path.strip()
    result: list[tuple[str, str, str, str]] = []
    for line in values:
        key, separator, raw_url = line.partition(" ")
        if not separator or not raw_url.strip():
            raise GitRepositoryError("submodule URL configuration is malformed")
        name = key.removeprefix("submodule.").removesuffix(".url")
        path = path_by_name.get(name)
        if path is None or not path.strip():
            raise GitRepositoryError("submodule URL and path configuration is ambiguous")
        path = path.strip()
        path_parts = Path(path).parts
        normalized_path = Path(path)
        if (
            not path
            or normalized_path == Path(".")
            or normalized_path.is_absolute()
            or ".." in path_parts
        ):
            raise GitRepositoryError("submodule path escapes the repository")
        candidate = _resolve_submodule_url(origin, raw_url)
        normalized, host, _project = _normalize_origin(candidate)
        result.append((name, path, normalized, host))
    return tuple(result)


def submodule_urls(root: Path | str) -> tuple[tuple[str, str, str], ...]:
    """Return the declared submodule paths and canonical HTTPS identities."""
    return tuple((path, url, host) for _name, path, url, host in _submodule_entries(root))


def submodule_update_arguments(
    root: Path, *paths: str, allowed_hosts: Sequence[str]
) -> tuple[str, ...]:
    """Override transport for this child only, without editing Git configuration."""
    owner = Path(
        _stdout(
            _probe(root, ("rev-parse", "--show-toplevel"), "git.submodule.owner"), "root"
        ).strip()
    ).resolve()
    entries = _validated_submodule_entries(owner, allowed_hosts=allowed_hosts)
    requested = {(root / path).resolve() for path in paths}
    entries = tuple(
        entry for entry in entries if not requested or (owner / entry[1]).resolve() in requested
    )
    controls = tuple(
        arg for name, _path, url, _host in entries for arg in ("-c", f"submodule.{name}.url={url}")
    )
    rewrites = _cached_submodule_origin_rewrites(owner, entries)
    return (*controls, *rewrites, "submodule", "update", "--init", "--", *paths)


def submodule_update_target(root: Path, path: str) -> tuple[Path, str]:
    """Select the deepest populated repository that owns a missing submodule path."""
    parts = Path(path).parts
    for index in range(len(parts) - 1, 0, -1):
        owner = root.joinpath(*parts[:index])
        if (owner / ".git").exists():
            return owner, str(Path(*parts[index:]))
    return root, path


def submodule_recursive_update_arguments(
    root: Path, *paths: str, allowed_hosts: Sequence[str]
) -> tuple[str, ...]:
    """Update selected submodules and recurse only within those paths."""
    arguments = submodule_update_arguments(root, *paths, allowed_hosts=allowed_hosts)
    separator = arguments.index("--")
    return (*arguments[:separator], "--recursive", *arguments[separator:])


def _cached_submodule_origin_rewrites(
    owner: Path, entries: Sequence[tuple[str, str, str, str]]
) -> tuple[str, ...]:
    """Validate cached origins and return only their transport rewrite controls."""
    # Cached origins may preserve a different SSH spelling than .gitmodules.
    cache_root = Path(
        _stdout(
            _probe(owner, ("rev-parse", "--git-path", "modules"), "git.submodule.cache-root"),
            "submodule cache directory",
        ).strip()
    )
    cache_root = (owner / cache_root).resolve()
    rewrites: list[str] = []
    for name, _path, url, _host in entries:
        cached = (cache_root / name).resolve()
        if not cached.is_relative_to(cache_root) or (cached / "config").is_symlink():
            raise GitRepositoryError("cached submodule directory is unsafe")
        if not cached.is_dir():
            continue
        raw_origins = _stdout(
            _probe(
                owner,
                ("--git-dir", str(cached), "config", "--local", "--get-all", "remote.origin.url"),
                "git.submodule.cached-origin",
            ),
            "cached submodule origin",
        ).splitlines()
        if len(raw_origins) != 1:
            raise GitRepositoryError("cached submodule origin is absent or ambiguous")
        raw_origin = raw_origins[0].strip()
        if _normalize_origin(_resolve_submodule_url("", raw_origin))[0] != url:
            raise GitRepositoryError("cached submodule origin does not match declared repository")
        rewrites.extend(("-c", f"url.{url}.insteadOf={raw_origin}"))
    return tuple(rewrites)


def submodule_recursive_transport_arguments(hosts: Sequence[str]) -> tuple[str, ...]:
    """Capture transport rewrites for one recursive, credentialed update."""
    controls: list[str] = []
    for host in sorted(set(hosts)):
        https = f"https://{host}/"
        controls.extend(
            (
                "-c",
                f"url.{https}.insteadOf=git@{host}:",
                "-c",
                f"url.{https}.insteadOf=ssh://git@{host}/",
                "-c",
                f"url.{https}.insteadOf=ssh://git@{host}:22/",
            )
        )
    return (*controls, "submodule", "update", "--init", "--recursive")


def submodule_frontier_transport_arguments(hosts: Sequence[str]) -> tuple[str, ...]:
    """Capture a one-level update for every currently initialized frontier."""
    recursive = submodule_recursive_transport_arguments(hosts)
    separator = recursive.index("submodule")
    controls = recursive[:separator]
    return (*controls, "submodule", "foreach", "--recursive", "git submodule update --init")


def validate_submodule_cache(root: Path, *, allowed_hosts: Sequence[str]) -> None:
    """Validate cached submodule identities without returning executable argv."""
    checkout = Path(root).expanduser().resolve()
    entries = _validated_submodule_entries(checkout, allowed_hosts=allowed_hosts)
    _cached_submodule_origin_rewrites(checkout, entries)


def _validate_local_submodule_url_overrides(
    root: Path, entries: Sequence[tuple[str, str, str, str]], origin: str
) -> None:
    """Reject local URL overrides that redirect a declared logical submodule."""
    configured = _configured_values(root, r"^submodule\..*\.url$", "git.submodule.local-urls")
    if not configured:
        return
    committed = {name: url for name, _path, url, _host in entries}
    for line in configured:
        key, separator, value = line.partition(" ")
        name = key.removeprefix("submodule.").removesuffix(".url")
        if not separator or name not in committed:
            raise GitRepositoryError("local submodule URL override is unmapped")
        expected = committed[name]
        actual = _resolve_submodule_url(origin, value)
        if _normalize_origin(expected)[0] != _normalize_origin(actual)[0]:
            raise GitRepositoryError("local submodule URL override is unsafe")


def _validated_submodule_entries(
    root: Path | str,
    *,
    allowed_hosts: Sequence[str],
) -> tuple[tuple[str, str, str, str], ...]:
    """Reject repository-controlled credential and command-routing overrides."""
    checkout = Path(root).expanduser().resolve()
    entries = _submodule_entries(checkout)
    dangerous = _configured_values(
        checkout,
        r"^(credential\.helper|core\.hooksPath|submodule\..*\.update|protocol\..*|url\..*\.insteadof|http.*|remote\..*\.proxy)$",
        "git.submodule.policy",
    )
    if dangerous:
        raise GitRepositoryError("repository-controlled submodule transport is unsafe")
    modules = checkout / ".gitmodules"
    if modules.is_symlink():
        raise GitRepositoryError("submodule configuration is not repository-owned")
    if modules.is_file():
        origin = _normalize_origin(
            _resolve_submodule_url(
                "",
                _stdout(
                    _probe(checkout, ("remote", "get-url", "origin"), "git.submodule.origin"),
                    "origin",
                ).strip(),
            )
        )[0]
        _validate_local_submodule_url_overrides(checkout, entries, origin)
        configured = _probe(
            checkout,
            (
                "config",
                "--file",
                str(modules),
                "--get-regexp",
                r"^(credential\.helper|core\.hooksPath|submodule\..*\.update|protocol\..*|url\..*\.insteadof|http.*|remote\..*\.proxy)$",
            ),
            "git.submodule.file-policy",
        )
        if configured.returncode not in (0, 1) or not isinstance(configured.stdout, str):
            raise GitRepositoryError("repository submodule policy is unavailable")
        if configured.stdout.strip():
            raise GitRepositoryError("repository-controlled submodule transport is unsafe")
    allowed = frozenset(allowed_hosts)
    if any(host not in allowed for _name, _path, _url, host in entries):
        raise GitRepositoryError("submodule host has no selected-member credential mapping")
    return entries


def validate_submodule_configuration(
    root: Path | str, *, allowed_hosts: Sequence[str]
) -> tuple[tuple[str, str, str], ...]:
    """Validate one frontier and return its canonical HTTPS identities."""
    return tuple(
        (path, url, host)
        for _name, path, url, host in _validated_submodule_entries(
            root, allowed_hosts=allowed_hosts
        )
    )


def validate_credentialed_transport_configuration(root: Path | str) -> None:
    """Reject repository-local controls that can redirect credentialed Git."""
    checkout = Path(root).expanduser().resolve()
    dangerous = _configured_values(
        checkout,
        r"^(credential\.helper|core\.(hookspath|fsmonitor|sshcommand)|protocol\..*|url\..*\.(insteadof|pushinsteadof)|http\..*|remote\..*\.(proxy|pushurl))$",
        "git.credentialed-transport.policy",
    )
    if dangerous:
        raise GitRepositoryError("repository-controlled credentialed transport is unsafe")


def credentialed_git_args(
    args: Sequence[str], environment: Mapping[str, str] | None, hooks_path: str | None = None
) -> tuple[str, ...]:
    """Disable repository-controlled credential helpers and unsafe protocols."""
    controls: list[str] = []
    if hooks_path is not None or (environment or {}).get("ODCLI_GITLAB_TOKEN_FILE"):
        for key in (
            "credential.helper=",
            "core.fsmonitor=false",
            "protocol.ssh.allow=never",
            "protocol.ext.allow=never",
        ):
            controls.extend(("-c", key))
    if hooks_path is not None:
        controls.extend(("-c", f"core.hooksPath={hooks_path}"))
    return (*controls, *tuple(args))


def missing_submodules(root: Path | str) -> tuple[str, ...]:
    """Return only unpopulated submodule paths from Git's recursive status."""
    checkout = Path(root).expanduser().resolve()
    result = _probe(checkout, ("submodule", "status", "--recursive"), "git.submodule.status")
    if result.returncode != 0 or not isinstance(result.stdout, str):
        raise GitRepositoryError("submodule status is unavailable")
    missing: list[str] = []
    for line in result.stdout.splitlines():
        if not line.startswith("-"):
            continue
        fields = line[1:].strip().split(maxsplit=1)
        if len(fields) == 2:
            missing.append(fields[1].split(" ", 1)[0])
    return tuple(dict.fromkeys(missing))


__all__ = [
    "missing_submodules",
    "normalize_repository_url",
    "repository_project_path",
    "repository_push_origin",
    "resolve_generic_repository",
    "resolve_repository",
    "revalidate_repository",
    "submodule_frontier_transport_arguments",
    "submodule_recursive_transport_arguments",
    "submodule_recursive_update_arguments",
    "submodule_update_target",
    "submodule_urls",
    "validate_credentialed_transport_configuration",
    "validate_submodule_cache",
    "validate_submodule_configuration",
]
