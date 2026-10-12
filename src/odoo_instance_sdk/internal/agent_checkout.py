"""Private numbered-tool allocation and caller-owned checkout coordination."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from odoo_instance_sdk.internal import (
    git_worktree as _git_worktree,
    numbered_command_plan as _command_plan,
)
from odoo_instance_sdk.internal.alternate_tool import (
    IDENTITY_RE,
    AlternateIdentity,
    AlternateManifest,
    inspect_alternate_tool,
    install_alternate_tool,
    lifecycle_lock,
    list_alternate_tools,
    read_alternate_manifest,
    remove_alternate_tool,
)
from odoo_instance_sdk.internal.checkout_cleanup import (
    DELETION_ROOT,
    BoundCleanupError,
    quarantine_directory as _quarantine_checkout,
    read_recovery_reservation,
    remove_bound_directory,
)
from odoo_instance_sdk.internal.numbered_command_plan import recovery_payload as _recovery_payload
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
    "quarantine quarantine_identity checkout_identity require_pushed".split()
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


def _parse_checkout_identity(value: str, *, message: str) -> _CheckoutIdentity:
    try:
        device, inode = value.split(":")
        return (int(device), int(inode))
    except ValueError:
        raise AgentCheckoutError(message) from None


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
    if not reference.is_dir() or _git_worktree.rev_parse_toplevel(reference) != reference.resolve():
        raise AgentCheckoutError("reference checkout is not its canonical Git root")
    raw_source = _git_worktree.remote_url(reference)
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
        branch = _git_worktree.rev_parse_branch(checkout)
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
    if _git_worktree.rev_parse_toplevel(checkout) != checkout.resolve():
        raise AgentCheckoutError("linked checkout is not its canonical Git root")
    canonical_repository = _canonical_repository_identity(repository)
    marker = _read_marker(checkout)
    if marker != _marker_payload(number, canonical_repository, sha):
        raise AgentCheckoutError("linked checkout marker does not match manifest")
    raw_origin = _git_worktree.remote_url(checkout)
    if raw_origin is None or _canonical_repository_identity(raw_origin) != canonical_repository:
        raise AgentCheckoutError("linked checkout origin does not match manifest")
    if _git(("merge-base", sha, "HEAD"), cwd=checkout, step_id="checkout-verify-ancestry") != sha:
        raise AgentCheckoutError("linked checkout HEAD is not based on manifest")
    if _git(
        ("status", "--porcelain", "--untracked-files=all"),
        cwd=checkout,
        step_id="checkout-verify-status",
    ):
        raise AgentCheckoutError("linked checkout is dirty")
    if require_pushed:
        branch = _git_worktree.rev_parse_branch(checkout)
        upstream_remote = _git(
            ("config", "--get", f"branch.{branch}.remote"),
            cwd=checkout,
            step_id="checkout-verify-upstream-remote",
        )
        if not upstream_remote or upstream_remote == ".":
            raise AgentCheckoutError("linked checkout has no verified non-local upstream")
        raw_upstream = _git_worktree.remote_url(checkout, upstream_remote)
        if (
            raw_upstream is None
            or _canonical_repository_identity(raw_upstream) != canonical_repository
        ):
            raise AgentCheckoutError("linked checkout upstream remote does not match manifest")
        merge_ref = _git(
            ("config", "--get", f"branch.{branch}.merge"),
            cwd=checkout,
            step_id="checkout-verify-upstream-ref",
        )
        if not merge_ref.startswith("refs/heads/"):
            raise AgentCheckoutError("linked checkout upstream ref is invalid")
        head = _git(("rev-parse", "HEAD"), cwd=checkout, step_id="checkout-verify-head")
        if _git_worktree._remote_ref_sha(checkout, raw_upstream, merge_ref) != head:
            raise AgentCheckoutError("linked checkout HEAD is not published")
    return marker


def _environment_interpreter(
    environment: DevelopmentEnvironment, *, require_exists: bool = True
) -> Path:
    if not environment.python_environment_owned:
        raise AgentCheckoutError("adopted environment does not own its Python environment")
    root = Path(environment.python_environment_path)
    interpreter = root if root.name == "python" else root / "bin" / "python"
    if require_exists and (interpreter.is_symlink() or not interpreter.is_file()):
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
    identity_value: AlternateIdentity,
    checkout: Path,
    reference_sha: str,
    sdk_sha: str,
) -> Path:
    path = identity_value.manager_root / RESERVATION_NAME
    _write_json(
        path,
        {
            "version": 1,
            "kind": "numbered",
            "identifier": identity_value.identifier,
            "checkout": str(checkout),
            "reference_sha": reference_sha,
            "sdk_sha": sdk_sha,
        },
    )
    return path


def _execute_create_plan(  # noqa: C901 -- fixed allocation/adoption transaction
    plan: _command_plan._CreateCommandPlan,
) -> NumberedInstallation:
    plan.identity.manager_root.mkdir(mode=0o700, parents=True, exist_ok=False)
    reservation = _reservation(plan.identity, plan.checkout, plan.reference_sha, plan.sdk_sha)
    marker: Path | None = None
    checkout_identity: _CheckoutIdentity | None = None
    environment: DevelopmentEnvironment | None = None
    installed = False
    old_root = os.environ.get(USER_ROOT_ENV)
    os.environ[USER_ROOT_ENV] = str(plan.identity.state_root)
    try:
        marker = _clone_checkout(
            plan.reference,
            plan.checkout,
            number=plan.number,
            repository=plan.repository,
            sha=plan.reference_sha,
        )
        checkout_identity = _checkout_identity(plan.checkout)
        environment = plan.client.environments.adopt(
            plan.project,
            plan.checkout,
            options=plan.environment_options(),
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
            _recovery_payload(
                plan.number,
                plan.checkout,
                plan.repository,
                plan.reference_sha,
                plan.sdk_sha,
                environment,
                recovery_interpreter,
                plan.environment_hash_lock_sha256,
                checkout_identity,
                False,
            ),
        )
        if environment.state is not EnvironmentState.READY:
            raise AgentCheckoutError("adoption did not return a ready environment")  # noqa: TRY301
        interpreter = _environment_interpreter(environment)
        if Path(environment.worktree_path).resolve() != plan.checkout.resolve():
            raise AgentCheckoutError(  # noqa: TRY301
                "adopted environment is not bound to the reserved checkout"
            )
        if (
            environment.checkout_repository_root is not None
            and Path(environment.checkout_repository_root).resolve() != plan.checkout.resolve()
        ):
            raise AgentCheckoutError("adopted environment checkout identity drifted")  # noqa: TRY301
        if (
            Path(environment.dependency_lock_path).is_symlink()
            or not Path(environment.dependency_lock_path).is_file()
        ):
            raise AgentCheckoutError(  # noqa: TRY301
                "adopted environment dependency lock is missing or unsafe"
            )
        if _sha256(Path(environment.dependency_lock_path)) != plan.environment_hash_lock_sha256:
            raise AgentCheckoutError(  # noqa: TRY301
                "adopted environment dependency lock digest drifted"
            )
        manifest = install_alternate_tool(
            plan.identity,
            plan.sdk_sha,
            checkout_path=str(plan.checkout),
            checkout_repository=plan.repository,
            checkout_sha=plan.reference_sha,
            environment_id=str(environment.id),
            environment_path=environment.python_environment_path,
            environment_lock=environment.dependency_lock_path,
            interpreter_path=str(interpreter),
            hash_lock_sha256=plan.environment_hash_lock_sha256,
        )
        installed = True
        reservation.unlink(missing_ok=True)
        return NumberedInstallation(plan.identity, manifest, environment, marker)
    except BaseException:
        if environment is not None:
            plan.client.environments.remove(environment)
        if installed:
            remove_alternate_tool(plan.identity)
        if marker is not None and checkout_identity is not None:
            _remove_owned_checkout(plan.checkout, checkout_identity)
        with contextlib.suppress(OSError):
            reservation.unlink()
        plan.identity.manager_root.rmdir()
        raise
    finally:
        if old_root is None:
            os.environ.pop(USER_ROOT_ENV, None)
        else:
            os.environ[USER_ROOT_ENV] = old_root


def create_numbered_installation(options: NumberedCheckoutOptions) -> NumberedInstallation:
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
        plan = _command_plan._CreateCommandPlan.from_options(
            client=options.client,
            project=options.project,
            environment_options=options.environment_options,
            home=home,
            number=number,
            identity=identity_value,
            checkout=checkout,
            reference=reference,
            checkout_parent=checkout_parent,
            repository=repository,
            reference_sha=options.reference_sha,
            sdk_sha=options.sdk_sha,
        )
        return _execute_create_plan(plan)


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
    client: OdooClient,
    checkout_parent: Path | None,
) -> None:
    try:
        payload = read_recovery_reservation(
            reservation,
            number=number,
            home=identity_value.home,
            fields=RECOVERY_RESERVATION_FIELDS,
            canonical_repository=_canonical_repository_identity,
            sha_re=SHA_RE,
            hash_re=HASH_RE,
        )
    except BoundCleanupError as exc:
        raise AgentCheckoutError(str(exc)) from exc
    checkout_value = str(payload["checkout"])
    repository_value = str(payload["repository"])
    sha_value = str(payload["checkout_sha"])
    environment_id_value = str(payload["environment_id"])
    environment_path_value = str(payload["environment_path"])
    environment_lock_value = str(payload["environment_lock"])
    interpreter_path_value = str(payload["interpreter_path"])
    digest_value = str(payload["hash_lock_sha256"])
    checkout_identity_value = str(payload["checkout_identity"])
    require_pushed_value = payload["require_pushed"]
    assert isinstance(require_pushed_value, bool)
    expected_checkout_identity = _parse_checkout_identity(
        checkout_identity_value, message="numbered recovery checkout identity is invalid"
    )
    linked_checkout = Path(checkout_value)
    quarantine_value = str(payload["quarantine"])
    quarantine_identity = str(payload["quarantine_identity"])
    expected_recovery: _CheckoutIdentity | None = None
    recovery_quarantine: Path | None = None
    if quarantine_value:
        recovery_quarantine = Path(quarantine_value)
        if recovery_quarantine.is_symlink() or not recovery_quarantine.is_dir():
            raise AgentCheckoutError("numbered recovery quarantine evidence is unavailable")
        checkout = linked_checkout
        expected_recovery = _parse_checkout_identity(
            quarantine_identity, message="numbered recovery quarantine identity is invalid"
        )
        if (
            recovery_quarantine.parent.name != DELETION_ROOT
            or recovery_quarantine.parent.parent.resolve() != linked_checkout.parent.resolve()
        ):
            raise AgentCheckoutError("numbered recovery quarantine path drifted")
    elif quarantine_identity:
        raise AgentCheckoutError("numbered recovery quarantine evidence is incomplete")
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
            require_pushed=require_pushed_value,
            checkout_parent=checkout_parent,
        )
        checkout_identity = _checkout_identity(checkout)
        if checkout_identity != expected_checkout_identity:
            raise AgentCheckoutError("recovery checkout identity changed before cleanup")
    else:
        assert expected_recovery is not None
        if expected_recovery != expected_checkout_identity:
            raise AgentCheckoutError("recovery checkout identity evidence drifted")
        checkout_identity = expected_recovery
    if expected_recovery is not None and checkout_identity != expected_recovery:
        raise AgentCheckoutError("numbered recovery quarantine identity drifted")
    environment = client.environments.get(environment_id)
    if str(environment.id) != environment_id:
        raise AgentCheckoutError("recovery environment identity does not match reservation")
    if Path(environment.worktree_path).resolve() != linked_checkout.resolve():
        raise AgentCheckoutError("recovery checkout linkage drifted")
    environment_root = Path(environment.python_environment_path)
    if environment_root.resolve() != Path(environment_path_value).resolve():
        raise AgentCheckoutError("recovery environment path drifted")
    if Path(environment.dependency_lock_path).resolve() != Path(environment_lock_value).resolve():
        raise AgentCheckoutError("recovery dependency lock path drifted")
    environment_removed = str(environment.state) == EnvironmentState.REMOVED.value
    interpreter = _environment_interpreter(environment, require_exists=not environment_removed)
    if str(interpreter) != str(Path(interpreter_path_value).resolve()):
        raise AgentCheckoutError("recovery interpreter identity drifted")
    if environment_removed and (
        _lexists(environment_root) or _lexists(Path(environment_lock_value))
    ):
        raise AgentCheckoutError("removed environment artifacts unexpectedly survive")
    if not environment_removed and _sha256(Path(environment_lock_value)) != digest_value:
        raise AgentCheckoutError("recovery dependency lock digest drifted")
    old_root = os.environ.get(USER_ROOT_ENV)
    os.environ[USER_ROOT_ENV] = str(identity_value.state_root)
    try:
        if not environment_removed:
            client.environments.remove(environment)
        if recovery_quarantine is None:
            _validate_checkout(
                checkout,
                number=number,
                repository=repository,
                sha=sha,
                require_pushed=require_pushed_value,
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
        if identity_value.manifest.is_file():
            remove_alternate_tool(identity_value, _locked=True)
        else:
            identity_value.lock.unlink(missing_ok=True)
        reservation.unlink(missing_ok=True)
        identity_value.manager_root.rmdir()
    finally:
        if old_root is None:
            os.environ.pop(USER_ROOT_ENV, None)
        else:
            os.environ[USER_ROOT_ENV] = old_root


def _execute_remove_plan(  # noqa: C901 -- fixed validation, cleanup and recovery sequence
    plan: _command_plan._RemoveCommandPlan,
) -> None:
    fields = (
        plan.manifest.checkout_path,
        plan.manifest.checkout_repository,
        plan.manifest.checkout_sha,
        plan.manifest.environment_id,
        plan.manifest.environment_path,
        plan.manifest.environment_lock,
        plan.manifest.interpreter_path,
        plan.manifest.hash_lock_sha256,
    )
    reservation = plan.identity.manager_root / RESERVATION_NAME
    if _lexists(reservation):
        _remove_reserved_installation(
            plan.number,
            plan.identity,
            reservation,
            plan.client,
            plan.checkout_parent,
        )
        return
    if not all(fields):
        raise AgentCheckoutError("numbered manifest has incomplete checkout linkage")
    checkout = Path(plan.manifest.checkout_path or "").expanduser()
    if not checkout.is_absolute():
        raise AgentCheckoutError("linked checkout path must be absolute")
    _validate_checkout(
        checkout,
        number=plan.number,
        repository=plan.manifest.checkout_repository or "",
        sha=plan.manifest.checkout_sha or "",
        require_pushed=True,
        checkout_parent=plan.checkout_parent,
    )
    checkout_identity = _checkout_identity(checkout)
    environment_id = plan.manifest.environment_id
    environment_path = plan.manifest.environment_path
    if environment_id is None or environment_path is None:
        raise AgentCheckoutError("numbered manifest has incomplete environment identity")
    environment = plan.client.environments.get(environment_id)
    if str(environment.id) != environment_id:
        raise AgentCheckoutError("environment identity does not match numbered manifest")
    checkout = checkout.resolve()
    if Path(environment.worktree_path).resolve() != checkout:
        raise AgentCheckoutError("environment checkout linkage drifted")
    if Path(environment.python_environment_path).resolve() != Path(environment_path).resolve():
        raise AgentCheckoutError("environment Python linkage drifted")
    recovery_payload = _recovery_payload(
        plan.number,
        checkout,
        plan.manifest.checkout_repository or "",
        plan.manifest.checkout_sha or "",
        plan.manifest.sha,
        environment,
        Path(plan.manifest.interpreter_path or "").resolve(),
        plan.manifest.hash_lock_sha256 or "",
        checkout_identity,
        True,
    )
    _write_json(reservation, recovery_payload)
    old_root = os.environ.get(USER_ROOT_ENV)
    os.environ[USER_ROOT_ENV] = str(plan.identity.state_root)
    try:
        if str(environment.state) != EnvironmentState.REMOVED.value:
            plan.client.environments.remove(environment)
        if (
            checkout.is_symlink()
            or not checkout.is_dir()
            or not _contained(checkout, checkout.parent)
        ):
            raise AgentCheckoutError("linked checkout became unsafe before deletion")
        _validate_checkout(
            checkout,
            number=plan.number,
            repository=plan.manifest.checkout_repository or "",
            sha=plan.manifest.checkout_sha or "",
            require_pushed=True,
            checkout_parent=plan.checkout_parent,
        )
        current_identity = _checkout_identity(checkout)
        if current_identity != checkout_identity:
            raise AgentCheckoutError("linked checkout identity changed after environment cleanup")
        _remove_owned_checkout(
            checkout,
            checkout_identity,
            recovery_reservation=reservation,
            recovery_payload=recovery_payload,
        )
        remove_alternate_tool(plan.identity, _locked=True)
        reservation.unlink(missing_ok=True)
        plan.identity.manager_root.rmdir()
    finally:
        if old_root is None:
            os.environ.pop(USER_ROOT_ENV, None)
        else:
            os.environ[USER_ROOT_ENV] = old_root


def remove_numbered_installation(
    number: str,
    *,
    client: OdooClient,
    home: Path | None = None,
    checkout_parent: Path | None = None,
) -> None:
    home_path = (home or Path.home()).expanduser().resolve()
    identity_value = AlternateIdentity("numbered", number, home_path)
    with allocation_lock(home_path), lifecycle_lock(identity_value, exclusive=True):
        reservation = identity_value.manager_root / RESERVATION_NAME
        if (manifest := read_alternate_manifest(identity_value, strict=False)) is None:
            if not _lexists(reservation):
                raise AgentCheckoutError("numbered manifest is missing")
            _remove_reserved_installation(
                number, identity_value, reservation, client, checkout_parent
            )
            return
        plan = _command_plan._RemoveCommandPlan(
            client, home_path, number, identity_value, checkout_parent, manifest
        )
        _execute_remove_plan(plan)
