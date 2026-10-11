"""Private numbered-tool allocation and caller-owned checkout coordination.

This module is intentionally a narrow boundary.  The alternate-tool module
owns launcher installation and removal; ``EnvironmentResource`` owns every
environment mutation.  This module only joins those two lifecycles around a
marked, independently cloned checkout.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from odoo_instance_sdk.internal.alternate_tool import (
    IDENTITY_RE,
    AlternateIdentity,
    AlternateManifest,
    inspect_alternate_tool,
    install_alternate_tool,
    lifecycle_lock,
    list_alternate_tools,
    publish_alternate_linkage,
    read_alternate_manifest,
    remove_alternate_tool,
)
from odoo_instance_sdk.internal.checkout_cleanup import (
    DELETION_ROOT,
    BoundCleanupError,
    quarantine_directory as _quarantine_checkout,
    remove_bound_directory,
    validate_recovery_payload,
)
from odoo_instance_sdk.internal.git_worktree import remote_url, rev_parse_branch, rev_parse_toplevel
from odoo_instance_sdk.internal.paths import USER_ROOT_ENV
from odoo_instance_sdk.internal.proc import ProcessExecutionError, run_captured
from odoo_instance_sdk.models import (
    DevelopmentEnvironment,
    EnvironmentDatabaseMode,
    EnvironmentState,
)
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.resources.environment import EnvironmentCheckoutOptions

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient

CHECKOUT_MARKER = ".odcli-checkout.json"
RESERVATION_NAME = "reservation.json"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
MARKER_VERSION = 1
RECOVERY_RESERVATION_FIELDS = frozenset(
    "version kind identifier checkout repository checkout_sha reference_sha sdk_sha "  # noqa: SIM905
    "environment_id environment_path environment_lock interpreter_path hash_lock_sha256 "
    "quarantine quarantine_identity".split()
)


class AgentCheckoutError(RuntimeError):
    pass


type _CheckoutIdentity = tuple[int, int]


def _checkout_identity(checkout: Path) -> _CheckoutIdentity:
    try:
        stat_result = checkout.stat(follow_symlinks=False)
    except OSError as exc:
        raise AgentCheckoutError("linked checkout identity is unreadable") from exc
    if not checkout.is_dir() or checkout.is_symlink():
        raise AgentCheckoutError("linked checkout is missing or a symlink")
    return (stat_result.st_dev, stat_result.st_ino)


def _canonical_repository_identity(source: str) -> str:  # noqa: C901 -- strict transport allowlist
    if not source or any(char.isspace() or ord(char) < 32 for char in source):
        raise AgentCheckoutError("repository origin contains unsafe characters")
    if "?" in source or "#" in source or source.startswith(("!", "ext::", "git+")):
        raise AgentCheckoutError("repository origin contains unsafe transport syntax")

    scp_match = re.fullmatch(r"(?P<user>[^@/:]+)@(?P<host>[^:/]+):(?P<path>[^?#]+)", source)
    if scp_match is not None:
        if scp_match.group("user") != "git":
            raise AgentCheckoutError("repository origin contains scp credentials")
        return f"ssh://git@{scp_match.group('host').lower()}/{scp_match.group('path').lstrip('/')}"

    parsed = urlsplit(source)
    if parsed.scheme:
        scheme = parsed.scheme.lower()
        if scheme not in {"https", "ssh"} or not parsed.path:
            raise AgentCheckoutError("repository origin uses an unsupported transport")
        if (
            parsed.username is not None
            and (scheme != "ssh" or parsed.username != "git" or parsed.password is not None)
        ) or (parsed.password is not None and parsed.username is None):
            raise AgentCheckoutError("repository origin contains credentials")
        if parsed.query or parsed.fragment:
            raise AgentCheckoutError("repository origin contains query or fragment data")
        try:
            hostname = parsed.hostname
            port = parsed.port
        except ValueError as exc:
            raise AgentCheckoutError("repository origin has an invalid authority") from exc
        if hostname is None:
            raise AgentCheckoutError("repository origin has no host")
        host = f"[{hostname.lower()}]" if ":" in hostname else hostname.lower()
        authority = host if port is None else f"{host}:{port}"
        user = "git@" if scheme == "ssh" and parsed.username == "git" else ""
        return f"{scheme}://{user}{authority}/{parsed.path.lstrip('/')}"

    path = Path(source)
    if path.is_absolute():
        return str(path.resolve())
    raise AgentCheckoutError("repository origin is not a canonical supported identity")


def _remove_owned_checkout(
    checkout: Path,
    expected: _CheckoutIdentity,
    *,
    recovery_reservation: Path | None = None,
    recovery_payload: dict[str, str | int] | None = None,
    recovery_quarantine: Path | None = None,
) -> None:
    parent = checkout.parent
    _ensure_no_symlink_components(parent, stop=(parent.anchor and Path(parent.anchor)) or parent)
    try:
        quarantine = _quarantine_checkout(
            checkout, expected, existing_quarantine=recovery_quarantine
        )
    except BoundCleanupError as exc:
        raise AgentCheckoutError(str(exc)) from exc
    parent_fd, quarantine_name, quarantine_expected = quarantine
    try:
        # The bound helper replaces pathname-based shutil.rmtree(...).
        if recovery_reservation is not None and recovery_payload is not None:
            _write_json(
                recovery_reservation,
                {
                    **recovery_payload,
                    "quarantine": str(checkout.parent / quarantine_name),
                    "quarantine_identity": f"{expected[0]}:{expected[1]}",
                },
            )
        remove_bound_directory(
            parent_fd,
            quarantine_name,
            checkout.name,
            quarantine_expected,
        )
    except BoundCleanupError as exc:
        raise AgentCheckoutError(str(exc)) from exc
    finally:
        os.close(parent_fd)


@dataclass(frozen=True, slots=True)
class NumberedCheckoutOptions:
    client: OdooClient
    project: ProjectConfig | Path
    reference_checkout: Path
    checkout_parent: Path
    reference_sha: str
    sdk_sha: str
    environment_options: EnvironmentCheckoutOptions
    home: Path | None = None
    source_repository: str | None = None

    def __post_init__(self) -> None:
        if not SHA_RE.fullmatch(self.reference_sha):
            raise ValueError("reference_sha must be a complete lowercase SHA")
        if not SHA_RE.fullmatch(self.sdk_sha):
            raise ValueError("sdk_sha must be a complete lowercase SHA")
        if not self.checkout_parent.is_absolute():
            raise ValueError("checkout_parent must be absolute")
        if not self.reference_checkout.is_absolute():
            raise ValueError("reference_checkout must be absolute")
        _validate_environment_options(self.environment_options)


@dataclass(frozen=True, slots=True)
class NumberedInstallation:
    identity: AlternateIdentity
    manifest: AlternateManifest
    environment: DevelopmentEnvironment
    checkout_marker: Path

    def to_json(self) -> dict[str, str | int | None]:
        return {
            "kind": "numbered",
            "identifier": self.identity.identifier,
            "launcher": str(self.identity.launcher),
            "state_root": str(self.identity.state_root),
            "checkout": str(self.checkout_marker.parent.parent),
            "checkout_sha": self.manifest.checkout_sha,
            "environment_id": str(self.environment.id),
            "environment_path": self.environment.python_environment_path,
            "interpreter": self.manifest.interpreter_path,
            "http_port": self.environment.http_port,
            "database": self.environment.target_db_name,
        }


def _validate_environment_options(options: EnvironmentCheckoutOptions) -> None:
    if options.db_mode is not EnvironmentDatabaseMode.COPY:
        raise ValueError("numbered adoption requires COPY mode")
    if options.base_ref is None:
        raise ValueError("numbered adoption requires an explicit base_ref")
    if options.python is None or not options.create_venv:
        raise ValueError("numbered adoption requires an SDK-owned Python environment")
    if options.hash_lock is None or not options.hash_lock_sha256:
        raise ValueError("numbered adoption requires an audited dependency lock and digest")
    if not HASH_RE.fullmatch(options.hash_lock_sha256):
        raise ValueError("hash_lock_sha256 must be a complete lowercase digest")
    if options.odoo_bin is None:
        raise ValueError("numbered adoption requires an explicit Odoo executable")
    if (options.remote_name is None) == (options.backup_id is None):
        raise ValueError("numbered adoption requires exactly one explicit COPY source")


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _ensure_no_symlink_components(path: Path, *, stop: Path) -> None:
    current = path
    stop = stop.resolve()
    while True:
        if current.is_symlink():
            raise AgentCheckoutError(f"refusing symlinked path: {current}")
        if current == stop or current.parent == current:
            return
        current = current.parent


def _contained(path: Path, root: Path) -> bool:
    try:
        return path.resolve(strict=False).is_relative_to(root.resolve())
    except OSError:
        return False


@contextlib.contextmanager
def allocation_lock(home: Path) -> Iterator[int]:
    root = home.expanduser().resolve() / ".local" / "share" / "odcli-slots"
    _ensure_no_symlink_components(root, stop=home.expanduser().resolve())
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = root / "allocation.lock"
    if path.is_symlink():
        raise AgentCheckoutError(f"refusing symlinked allocation lock: {path}")
    try:
        fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError as exc:
        raise AgentCheckoutError(f"cannot safely open allocation lock: {path}") from exc
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield fd
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        with contextlib.suppress(OSError):
            os.close(fd)


def _checkout_path(parent: Path, number: str) -> Path:
    parent = parent.expanduser().resolve()
    if not number or not IDENTITY_RE.fullmatch(number):
        raise ValueError("number must be a positive canonical decimal identity")
    return parent / f"odcli-{number}"


def _occupied(identity_value: AlternateIdentity, checkout: Path) -> bool:
    return any(
        _lexists(path)
        for path in (
            identity_value.launcher,
            identity_value.state_root,
            identity_value.manager_root,
            checkout,
        )
    )


def _next_number(home: Path, checkout_parent: Path) -> tuple[str, Path]:
    number = 1
    while True:
        text = str(number)
        identity_value = AlternateIdentity("numbered", text, home)
        checkout = _checkout_path(checkout_parent, text)
        if not _occupied(identity_value, checkout):
            return text, checkout
        number += 1


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def _git(
    args: tuple[str, ...],
    *,
    cwd: Path,
    step_id: str,
    mutating: bool = False,
) -> str:
    try:
        result = run_captured(
            ("git", *args),
            cwd=cwd,
            timeout=60.0,
            text=True,
            step_id=step_id,
            read_only=not mutating,
            mutating=mutating,
        )
    except ProcessExecutionError as exc:
        raise AgentCheckoutError(f"git {step_id} failed") from exc
    if result.returncode != 0:
        raise AgentCheckoutError(f"git {step_id} failed")
    return _text(result.stdout).strip()


def _safe_source(reference: Path, reference_sha: str, expected_remote: str | None) -> str:
    _ensure_no_symlink_components(
        reference,
        stop=(reference.anchor and Path(reference.anchor)) or reference,
    )
    if _lexists(reference) and reference.is_symlink():
        raise AgentCheckoutError("reference checkout is a symlink")
    if not reference.is_dir() or rev_parse_toplevel(reference) != reference.resolve():
        raise AgentCheckoutError("reference checkout is not its canonical Git root")
    raw_source = remote_url(reference)
    if raw_source is None:
        raise AgentCheckoutError("reference checkout has no origin")
    source = _canonical_repository_identity(raw_source)
    expected = (
        _canonical_repository_identity(expected_remote) if expected_remote is not None else None
    )
    if expected is not None and source != expected:
        raise AgentCheckoutError("reference checkout origin does not match configured source")
    resolved = _git(
        ("rev-parse", "--verify", f"{reference_sha}^{{commit}}"),
        cwd=reference,
        step_id="reference-sha",
    )
    if resolved != reference_sha:
        raise AgentCheckoutError("reference checkout SHA does not match configured SHA")
    if _git(
        ("status", "--porcelain", "--untracked-files=all"),
        cwd=reference,
        step_id="reference-status",
    ):
        raise AgentCheckoutError("reference checkout is dirty")
    return source


def _write_json(path: Path, payload: Mapping[str, str | int]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        temporary = Path(stream.name)
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def _marker_payload(number: str, repository: str, sha: str) -> dict[str, str | int]:
    return {
        "version": MARKER_VERSION,
        "kind": "numbered",
        "identifier": number,
        "repository": repository,
        "sha": sha,
    }


def _write_marker(checkout: Path, number: str, repository: str, sha: str) -> Path:
    marker = checkout / ".git" / CHECKOUT_MARKER
    _write_json(marker, _marker_payload(number, repository, sha))
    return marker


def _read_marker(checkout: Path) -> dict[str, str | int]:
    marker = checkout / ".git" / CHECKOUT_MARKER
    if marker.is_symlink() or not marker.is_file():
        raise AgentCheckoutError("numbered checkout marker is missing or unsafe")
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AgentCheckoutError("numbered checkout marker is unreadable") from exc
    if not isinstance(payload, dict) or any(not isinstance(k, str) for k in payload):
        raise AgentCheckoutError("numbered checkout marker is invalid")
    return payload


def _clone_checkout(
    reference: Path, checkout: Path, *, number: str, repository: str, sha: str
) -> Path:
    if not _contained(checkout, checkout.parent) or _lexists(checkout):
        raise AgentCheckoutError("checkout target is occupied or escapes its parent")
    checkout.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    owned_identity: _CheckoutIdentity | None = None
    try:
        _git(
            ("clone", "--local", "--no-hardlinks", str(reference), str(checkout)),
            cwd=reference,
            step_id="checkout-clone",
            mutating=True,
        )
        owned_identity = _checkout_identity(checkout)
        branch = rev_parse_branch(checkout)
        _git(("checkout", "-B", branch, sha), cwd=checkout, step_id="checkout-pin", mutating=True)
        _git(
            ("remote", "set-url", "origin", repository),
            cwd=checkout,
            step_id="checkout-origin",
            mutating=True,
        )
        marker = _write_marker(checkout, number, repository, sha)
        if _git(("rev-parse", "HEAD"), cwd=checkout, step_id="checkout-head") != sha:
            raise AgentCheckoutError(  # noqa: TRY301
                "cloned checkout did not retain the requested SHA"
            )
        if _git(
            ("status", "--porcelain", "--untracked-files=all"),
            cwd=checkout,
            step_id="checkout-status",
        ):
            raise AgentCheckoutError("cloned checkout is unexpectedly dirty")  # noqa: TRY301
        return marker  # noqa: TRY300
    except BaseException:
        if owned_identity is not None:
            with contextlib.suppress(AgentCheckoutError):
                _remove_owned_checkout(checkout, owned_identity)
        raise


def _validate_checkout(  # noqa: C901 -- fixed external Git guard sequence
    checkout: Path,
    *,
    number: str,
    repository: str,
    sha: str,
    require_pushed: bool,
    checkout_parent: Path | None = None,
) -> dict[str, str | int]:
    if checkout_parent is not None:
        parent = checkout_parent.expanduser().absolute()
        _ensure_no_symlink_components(
            parent,
            stop=(parent.anchor and Path(parent.anchor)) or parent,
        )
        if not _contained(checkout, parent) or checkout.parent.resolve() != parent.resolve():
            raise AgentCheckoutError("linked checkout escapes its configured parent")
    _ensure_no_symlink_components(
        checkout,
        stop=(checkout.anchor and Path(checkout.anchor)) or checkout,
    )
    if checkout.is_symlink() or not checkout.is_dir():
        raise AgentCheckoutError("linked checkout is missing or a symlink")
    if rev_parse_toplevel(checkout) != checkout.resolve():
        raise AgentCheckoutError("linked checkout is not its canonical Git root")
    canonical_repository = _canonical_repository_identity(repository)
    marker = _read_marker(checkout)
    if marker != _marker_payload(number, canonical_repository, sha):
        raise AgentCheckoutError("linked checkout marker does not match manifest")
    raw_origin = remote_url(checkout)
    if raw_origin is None or _canonical_repository_identity(raw_origin) != canonical_repository:
        raise AgentCheckoutError("linked checkout origin does not match manifest")
    if _git(("rev-parse", "HEAD"), cwd=checkout, step_id="checkout-verify-head") != sha:
        raise AgentCheckoutError("linked checkout HEAD does not match manifest")
    if _git(
        ("status", "--porcelain", "--untracked-files=all"),
        cwd=checkout,
        step_id="checkout-verify-status",
    ):
        raise AgentCheckoutError("linked checkout is dirty")
    if require_pushed:
        upstream = _git(
            ("rev-parse", "--abbrev-ref", "@{upstream}"),
            cwd=checkout,
            step_id="checkout-verify-upstream",
        )
        if not upstream:
            raise AgentCheckoutError("linked checkout has no verified upstream")
        counts = _git(
            ("rev-list", "--left-right", "--count", "@{upstream}...HEAD"),
            cwd=checkout,
            step_id="checkout-verify-pushed",
        )
        try:
            behind, ahead = (int(item) for item in counts.split())
        except (ValueError, TypeError) as exc:
            raise AgentCheckoutError("linked checkout push state is unreadable") from exc
        if ahead:
            raise AgentCheckoutError("linked checkout has unpushed commits")
        if behind:
            raise AgentCheckoutError("linked checkout is behind its upstream")
    return marker


def _environment_interpreter(environment: DevelopmentEnvironment) -> Path:
    if not environment.python_environment_owned:
        raise AgentCheckoutError("adopted environment does not own its Python environment")
    root = Path(environment.python_environment_path)
    interpreter = root if root.name == "python" else root / "bin" / "python"
    if interpreter.is_symlink() or not interpreter.is_file():
        raise AgentCheckoutError("adopted environment interpreter is missing or unsafe")
    return interpreter.resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise AgentCheckoutError("adopted environment dependency lock is unreadable") from exc
    return digest.hexdigest()


def _reservation(
    identity_value: AlternateIdentity, checkout: Path, options: NumberedCheckoutOptions
) -> Path:
    path = identity_value.manager_root / RESERVATION_NAME
    _write_json(
        path,
        {
            "version": 1,
            "kind": "numbered",
            "identifier": identity_value.identifier,
            "checkout": str(checkout),
            "reference_sha": options.reference_sha,
            "sdk_sha": options.sdk_sha,
        },
    )
    return path


def _read_recovery_reservation(
    reservation: Path, *, number: str, home: Path
) -> dict[str, str | int]:
    parent = reservation.parent
    _ensure_no_symlink_components(parent, stop=home)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        parent_fd = os.open(parent, flags)
    except OSError as exc:
        raise AgentCheckoutError("cannot safely open recovery reservation parent") from exc
    try:
        try:
            reservation_fd = os.open(
                reservation.name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
        except OSError as exc:
            raise AgentCheckoutError("recovery reservation is missing or symlinked") from exc
        try:
            if not stat.S_ISREG(os.fstat(reservation_fd).st_mode):
                raise AgentCheckoutError("recovery reservation is not a regular file")
            with os.fdopen(reservation_fd, encoding="utf-8") as stream:
                reservation_fd = -1
                payload = json.load(stream)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AgentCheckoutError("numbered recovery reservation is unreadable") from exc
        finally:
            if reservation_fd >= 0:
                os.close(reservation_fd)
    finally:
        os.close(parent_fd)

    try:
        return validate_recovery_payload(
            payload,
            fields=RECOVERY_RESERVATION_FIELDS,
            number=number,
            canonical_repository=_canonical_repository_identity,
            sha_re=SHA_RE,
            hash_re=HASH_RE,
        )
    except BoundCleanupError as exc:
        raise AgentCheckoutError(str(exc)) from exc


def create_numbered_installation(  # noqa: C901 -- fixed allocation/adoption transaction
    options: NumberedCheckoutOptions,
) -> NumberedInstallation:
    home = (options.home or Path.home()).expanduser().resolve()
    reference = options.reference_checkout.expanduser().absolute()
    checkout_parent = options.checkout_parent.expanduser().absolute()
    _ensure_no_symlink_components(
        checkout_parent,
        stop=(checkout_parent.anchor and Path(checkout_parent.anchor)) or checkout_parent,
    )
    _ensure_no_symlink_components(
        reference,
        stop=(reference.anchor and Path(reference.anchor)) or reference,
    )
    reference = reference.resolve()
    checkout_parent = checkout_parent.resolve()
    repository = _safe_source(reference, options.reference_sha, options.source_repository)
    with allocation_lock(home):
        number, checkout = _next_number(home, checkout_parent)
        identity_value = AlternateIdentity("numbered", number, home)
        identity_value.manager_root.mkdir(mode=0o700, parents=True, exist_ok=False)
        reservation = _reservation(identity_value, checkout, options)
        marker: Path | None = None
        checkout_identity: _CheckoutIdentity | None = None
        environment: DevelopmentEnvironment | None = None
        installed = False
        old_root = os.environ.get(USER_ROOT_ENV)
        os.environ[USER_ROOT_ENV] = str(identity_value.state_root)
        try:
            marker = _clone_checkout(
                reference,
                checkout,
                number=number,
                repository=repository,
                sha=options.reference_sha,
            )
            checkout_identity = _checkout_identity(checkout)
            install_alternate_tool(identity_value, options.sdk_sha)
            installed = True
            environment = options.client.environments.adopt(
                options.project,
                checkout,
                options=options.environment_options,
            )
            environment_root = Path(environment.python_environment_path)
            recovery_interpreter = (
                environment_root
                if environment_root.name == "python"
                else environment_root / "bin" / "python"
            )
            with contextlib.suppress(AgentCheckoutError):
                recovery_interpreter = _environment_interpreter(environment)
            _write_json(
                reservation,
                {
                    "version": 1,
                    "kind": "numbered",
                    "identifier": number,
                    "checkout": str(checkout),
                    "repository": repository,
                    "checkout_sha": options.reference_sha,
                    "reference_sha": options.reference_sha,
                    "sdk_sha": options.sdk_sha,
                    "environment_id": str(environment.id),
                    "environment_path": environment.python_environment_path,
                    "interpreter_path": str(recovery_interpreter),
                    "environment_lock": environment.dependency_lock_path,
                    "hash_lock_sha256": options.environment_options.hash_lock_sha256 or "",
                    "quarantine": "",
                    "quarantine_identity": "",
                },
            )
            if environment.state is not EnvironmentState.READY:
                raise AgentCheckoutError("adoption did not return a ready environment")  # noqa: TRY301
            interpreter = _environment_interpreter(environment)
            if Path(environment.worktree_path).resolve() != checkout.resolve():
                raise AgentCheckoutError(  # noqa: TRY301
                    "adopted environment is not bound to the reserved checkout"
                )
            if (
                environment.checkout_repository_root is not None
                and Path(environment.checkout_repository_root).resolve() != checkout.resolve()
            ):
                raise AgentCheckoutError("adopted environment checkout identity drifted")  # noqa: TRY301
            if (
                Path(environment.dependency_lock_path).is_symlink()
                or not Path(environment.dependency_lock_path).is_file()
            ):
                raise AgentCheckoutError(  # noqa: TRY301
                    "adopted environment dependency lock is missing or unsafe"
                )
            if (
                _sha256(Path(environment.dependency_lock_path))
                != options.environment_options.hash_lock_sha256
            ):
                raise AgentCheckoutError(  # noqa: TRY301
                    "adopted environment dependency lock digest drifted"
                )
            manifest = publish_alternate_linkage(
                identity_value,
                checkout_path=str(checkout),
                checkout_repository=repository,
                checkout_sha=options.reference_sha,
                environment_id=str(environment.id),
                environment_path=environment.python_environment_path,
                environment_lock=environment.dependency_lock_path,
                interpreter_path=str(interpreter),
                hash_lock_sha256=options.environment_options.hash_lock_sha256 or "",
            )
            reservation.unlink(missing_ok=True)
            return NumberedInstallation(identity_value, manifest, environment, marker)
        except BaseException:
            if environment is not None:
                options.client.environments.remove(environment)
            if installed:
                with contextlib.suppress(BaseException):
                    remove_alternate_tool(identity_value)
            if marker is not None and checkout_identity is not None:
                with contextlib.suppress(AgentCheckoutError):
                    _remove_owned_checkout(checkout, checkout_identity)
            with contextlib.suppress(OSError):
                reservation.unlink()
            with contextlib.suppress(OSError):
                identity_value.manager_root.rmdir()
            raise
        finally:
            if old_root is None:
                os.environ.pop(USER_ROOT_ENV, None)
            else:
                os.environ[USER_ROOT_ENV] = old_root


def inspect_numbered_installation(
    number: str, *, home: Path | None = None
) -> dict[str, str | int | bool | None]:
    identity_value = AlternateIdentity("numbered", number, home or Path.home())
    manifest = read_alternate_manifest(identity_value)
    assert manifest is not None
    if manifest.checkout_path is None:
        raise AgentCheckoutError("numbered manifest has no checkout linkage")
    _validate_checkout(
        Path(manifest.checkout_path),
        number=number,
        repository=manifest.checkout_repository or "",
        sha=manifest.checkout_sha or "",
        require_pushed=False,
    )
    return {
        **inspect_alternate_tool(identity_value),
        "checkout": manifest.checkout_path,
        "checkout_repository": manifest.checkout_repository,
        "checkout_sha": manifest.checkout_sha,
        "environment_id": manifest.environment_id,
        "environment_path": manifest.environment_path,
        "environment_lock": manifest.environment_lock,
        "interpreter_path": manifest.interpreter_path,
        "hash_lock_sha256": manifest.hash_lock_sha256,
    }


def list_numbered_installations(
    *, home: Path | None = None, limit: int = 100
) -> tuple[dict[str, str | int | bool | None], ...]:
    return tuple(list_alternate_tools("numbered", home=home, limit=limit))


def _remove_reserved_installation(  # noqa: C901 -- fixed reservation recovery sequence
    number: str,
    identity_value: AlternateIdentity,
    reservation: Path,
    *,
    client: OdooClient,
    checkout_parent: Path | None,
) -> None:
    payload = _read_recovery_reservation(
        reservation,
        number=number,
        home=identity_value.home,
    )
    checkout_value = str(payload["checkout"])
    repository_value = str(payload["repository"])
    sha_value = str(payload["checkout_sha"])
    environment_id_value = str(payload["environment_id"])
    environment_path_value = str(payload["environment_path"])
    environment_lock_value = str(payload["environment_lock"])
    interpreter_path_value = str(payload["interpreter_path"])
    digest_value = str(payload["hash_lock_sha256"])
    linked_checkout = Path(checkout_value)
    quarantine_value = str(payload["quarantine"])
    quarantine_identity = str(payload["quarantine_identity"])
    expected_recovery: _CheckoutIdentity | None = None
    recovery_quarantine: Path | None = None
    if quarantine_value and Path(quarantine_value).is_dir():
        recovery_quarantine = Path(quarantine_value)
        checkout = linked_checkout
        try:
            device_text, inode_text = quarantine_identity.split(":")
            expected_recovery = (int(device_text), int(inode_text))
        except ValueError:
            raise AgentCheckoutError("numbered recovery quarantine identity is invalid") from None
        if (
            recovery_quarantine.parent.name != DELETION_ROOT
            or recovery_quarantine.parent.parent.resolve() != linked_checkout.parent.resolve()
        ):
            raise AgentCheckoutError("numbered recovery quarantine path drifted")
    else:
        checkout = linked_checkout
    repository = repository_value
    sha = sha_value
    environment_id = environment_id_value
    if recovery_quarantine is None:
        _validate_checkout(
            checkout,
            number=number,
            repository=repository,
            sha=sha,
            require_pushed=False,
            checkout_parent=checkout_parent,
        )
        checkout_identity = _checkout_identity(checkout)
    else:
        assert expected_recovery is not None
        checkout_identity = expected_recovery
    if expected_recovery is not None and checkout_identity != expected_recovery:
        raise AgentCheckoutError("numbered recovery quarantine identity drifted")
    environment = client.environments.get(environment_id)
    if str(environment.id) != environment_id:
        raise AgentCheckoutError("recovery environment identity does not match reservation")
    if Path(environment.worktree_path).resolve() != linked_checkout.resolve():
        raise AgentCheckoutError("recovery checkout linkage drifted")
    if (
        Path(environment.python_environment_path).resolve()
        != Path(environment_path_value).resolve()
    ):
        raise AgentCheckoutError("recovery environment path drifted")
    if Path(environment.dependency_lock_path).resolve() != Path(environment_lock_value).resolve():
        raise AgentCheckoutError("recovery dependency lock path drifted")
    if str(_environment_interpreter(environment)) != str(Path(interpreter_path_value).resolve()):
        raise AgentCheckoutError("recovery interpreter identity drifted")
    if _sha256(Path(environment_lock_value)) != digest_value:
        raise AgentCheckoutError("recovery dependency lock digest drifted")
    old_root = os.environ.get(USER_ROOT_ENV)
    os.environ[USER_ROOT_ENV] = str(identity_value.state_root)
    try:
        if str(environment.state) != EnvironmentState.REMOVED.value:
            client.environments.remove(environment)
        if recovery_quarantine is None:
            _validate_checkout(
                checkout,
                number=number,
                repository=repository,
                sha=sha,
                require_pushed=False,
                checkout_parent=checkout_parent,
            )
            if _checkout_identity(checkout) != checkout_identity:
                raise AgentCheckoutError("recovery checkout identity changed after cleanup")
        _remove_owned_checkout(
            checkout,
            checkout_identity,
            recovery_reservation=reservation,
            recovery_payload=payload,
            recovery_quarantine=recovery_quarantine,
        )
        reservation.unlink(missing_ok=True)
        remove_alternate_tool(identity_value, _locked=True)
    finally:
        if old_root is None:
            os.environ.pop(USER_ROOT_ENV, None)
        else:
            os.environ[USER_ROOT_ENV] = old_root


def remove_numbered_installation(  # noqa: C901 -- fixed validation, cleanup and recovery sequence
    number: str,
    *,
    client: OdooClient,
    home: Path | None = None,
    checkout_parent: Path | None = None,
) -> None:
    home_path = (home or Path.home()).expanduser().resolve()
    identity_value = AlternateIdentity("numbered", number, home_path)
    with allocation_lock(home_path), lifecycle_lock(identity_value, exclusive=True):
        manifest = read_alternate_manifest(identity_value)
        assert manifest is not None
        fields = (
            manifest.checkout_path,
            manifest.checkout_repository,
            manifest.checkout_sha,
            manifest.environment_id,
            manifest.environment_path,
            manifest.environment_lock,
            manifest.interpreter_path,
            manifest.hash_lock_sha256,
        )
        reservation = identity_value.manager_root / RESERVATION_NAME
        if not all(fields) and _lexists(reservation):
            _remove_reserved_installation(
                number,
                identity_value,
                reservation,
                client=client,
                checkout_parent=checkout_parent,
            )
            return
        if not all(fields):
            raise AgentCheckoutError("numbered manifest has incomplete checkout linkage")
        checkout = Path(manifest.checkout_path or "").expanduser()
        if not checkout.is_absolute():
            raise AgentCheckoutError("linked checkout path must be absolute")
        _validate_checkout(
            checkout,
            number=number,
            repository=manifest.checkout_repository or "",
            sha=manifest.checkout_sha or "",
            require_pushed=True,
            checkout_parent=checkout_parent,
        )
        checkout_identity = _checkout_identity(checkout)
        environment_id = manifest.environment_id
        environment_path = manifest.environment_path
        if environment_id is None or environment_path is None:
            raise AgentCheckoutError("numbered manifest has incomplete environment identity")
        environment = client.environments.get(environment_id)
        if str(environment.id) != environment_id:
            raise AgentCheckoutError("environment identity does not match numbered manifest")
        checkout = checkout.resolve()
        if Path(environment.worktree_path).resolve() != checkout:
            raise AgentCheckoutError("environment checkout linkage drifted")
        if Path(environment.python_environment_path).resolve() != Path(environment_path).resolve():
            raise AgentCheckoutError("environment Python linkage drifted")
        old_root = os.environ.get(USER_ROOT_ENV)
        os.environ[USER_ROOT_ENV] = str(identity_value.state_root)
        try:
            if str(environment.state) != EnvironmentState.REMOVED.value:
                client.environments.remove(environment)
            if (
                checkout.is_symlink()
                or not checkout.is_dir()
                or not _contained(checkout, checkout.parent)
            ):
                raise AgentCheckoutError("linked checkout became unsafe before deletion")
            _validate_checkout(
                checkout,
                number=number,
                repository=manifest.checkout_repository or "",
                sha=manifest.checkout_sha or "",
                require_pushed=True,
                checkout_parent=checkout_parent,
            )
            current_identity = _checkout_identity(checkout)
            if current_identity != checkout_identity:
                raise AgentCheckoutError(
                    "linked checkout identity changed after environment cleanup"
                )
            _remove_owned_checkout(checkout, checkout_identity)
            reservation = identity_value.manager_root / RESERVATION_NAME
            reservation.unlink(missing_ok=True)
            remove_alternate_tool(identity_value, _locked=True)
        finally:
            if old_root is None:
                os.environ.pop(USER_ROOT_ENV, None)
            else:
                os.environ[USER_ROOT_ENV] = old_root


# Short private aliases used by service wrappers while keeping one implementation.
create_numbered_checkout = create_numbered_installation
inspect_numbered_checkout = inspect_numbered_installation
list_numbered_checkouts = list_numbered_installations
remove_numbered_checkout = remove_numbered_installation
