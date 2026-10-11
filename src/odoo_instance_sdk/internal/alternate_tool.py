"""Shared lifecycle for exact-revision alternate OdCLI launchers.

The numbered and hot-fix adapters deliberately live outside this module.  This
module owns only the mechanics that must never diverge: installation,
provenance, selector probing, publication, locking, inspection, and cleanup.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import shutil
import sys
import tempfile
import uuid
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, cast

from odoo_instance_sdk.internal.paths import USER_ROOT_ENV
from odoo_instance_sdk.internal.proc import ProcessResult, run_captured

REPOSITORY = "maximchikAlexandr/odoo-instance-sdk"
REPOSITORY_URL = f"https://github.com/{REPOSITORY}.git"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
IDENTITY_RE = re.compile(r"^[1-9][0-9]*$")
MANIFEST_VERSION = 1
LAUNCHER_KIND_ENV = "ODCLI_LAUNCHER_KIND"
LAUNCHER_ID_ENV = "ODCLI_LAUNCHER_ID"
LauncherKind = Literal["numbered", "hotfix"]


class AlternateToolError(RuntimeError):
    """An alternate-tool lifecycle operation cannot be completed safely."""


@dataclass(frozen=True, slots=True)
class AlternateIdentity:
    """One validated launcher identity and its deterministic filesystem layout."""

    kind: LauncherKind
    identifier: str
    home: Path

    def __post_init__(self) -> None:
        if self.kind not in ("numbered", "hotfix"):
            raise ValueError("launcher kind must be numbered or hotfix")
        _validate_identity(self.identifier)
        object.__setattr__(self, "home", self.home.expanduser().resolve())

    @property
    def launcher_name(self) -> str:
        prefix = "odcli" if self.kind == "numbered" else "odcli-fix"
        return f"{prefix}-{self.identifier}"

    @property
    def state_root(self) -> Path:
        prefix = ".odcli" if self.kind == "numbered" else ".odcli-fix"
        return self.home / f"{prefix}-{self.identifier}"

    @property
    def manager_root(self) -> Path:
        prefix = "odcli-slots" if self.kind == "numbered" else "odcli-fix-tools"
        return self.home / ".local" / "share" / prefix / self.identifier

    @property
    def tool_root(self) -> Path:
        return self.manager_root / "tool"

    @property
    def tool_bin(self) -> Path:
        return self.manager_root / "bin"

    @property
    def executable(self) -> Path:
        return self.tool_bin / "odcli"

    @property
    def launcher(self) -> Path:
        return self.home / ".local" / "bin" / self.launcher_name

    @property
    def manifest(self) -> Path:
        return self.manager_root / "manifest.json"

    @property
    def lock(self) -> Path:
        return self.manager_root / "lifecycle.lock"


@dataclass(frozen=True, slots=True)
class AlternateManifest:
    """The fixed, discriminated metadata published for one launcher."""

    identity: AlternateIdentity
    sha: str
    repository: str
    executable: Path
    shim: str
    pr: int | None = None
    review_sha: str | None = None
    review_session: str | None = None
    branch: str | None = None
    checkout_path: str | None = None
    checkout_repository: str | None = None
    checkout_sha: str | None = None
    environment_id: str | None = None
    environment_path: str | None = None
    environment_lock: str | None = None
    interpreter_path: str | None = None
    hash_lock_sha256: str | None = None

    def __post_init__(self) -> None:
        if not SHA_RE.fullmatch(self.sha):
            raise ValueError("revision must be a complete lowercase SHA")
        if self.repository != REPOSITORY_URL:
            raise ValueError("unsupported source repository")
        if self.identity.kind == "numbered" and any(
            value is not None
            for value in (self.pr, self.review_sha, self.review_session, self.branch)
        ):
            raise ValueError("numbered manifests cannot contain hot-fix fields")
        linkage = (
            self.checkout_path,
            self.checkout_repository,
            self.checkout_sha,
            self.environment_id,
            self.environment_path,
            self.environment_lock,
            self.interpreter_path,
            self.hash_lock_sha256,
        )
        if self.identity.kind == "numbered" and any(linkage) and not all(linkage):
            raise ValueError("numbered manifests require complete checkout linkage")
        if self.identity.kind == "hotfix":
            if self.pr is None or self.pr <= 0:
                raise ValueError("hot-fix manifests require a positive PR number")
            if self.review_sha != self.sha or not self.review_session:
                raise ValueError("hot-fix manifests require exact review provenance")
            if any(linkage):
                raise ValueError("hot-fix manifests cannot contain checkout linkage")

    def to_json(self) -> dict[str, str | int | None]:
        identity = self.identity
        payload: dict[str, str | int | None] = {
            "version": MANIFEST_VERSION,
            "kind": identity.kind,
            "identifier": identity.identifier,
            "sha": self.sha,
            "repository": self.repository,
            "state_root": str(identity.state_root),
            "manager_root": str(identity.manager_root),
            "tool_root": str(identity.tool_root),
            "tool_bin": str(identity.tool_bin),
            "executable": str(self.executable),
            "launcher": str(identity.launcher),
            "manifest": str(identity.manifest),
            "lock": str(identity.lock),
            "shim": self.shim,
            "pr": self.pr,
            "review_sha": self.review_sha,
            "review_session": self.review_session,
            "branch": self.branch,
        }
        if identity.kind == "numbered" and self.checkout_path is not None:
            payload.update(
                {
                    "checkout_path": self.checkout_path,
                    "checkout_repository": self.checkout_repository,
                    "checkout_sha": self.checkout_sha,
                    "environment_id": self.environment_id,
                    "environment_path": self.environment_path,
                    "environment_lock": self.environment_lock,
                    "interpreter_path": self.interpreter_path,
                    "hash_lock_sha256": self.hash_lock_sha256,
                }
            )
        return payload


def _validate_identity(identifier: str) -> None:
    if not isinstance(identifier, str) or not IDENTITY_RE.fullmatch(identifier):
        raise ValueError("identity must be a positive decimal number without leading zeroes")


def identity(kind: LauncherKind, identifier: str, *, home: Path | None = None) -> AlternateIdentity:
    """Build one deterministic identity without creating any filesystem state."""
    return AlternateIdentity(kind, identifier, home or Path.home())


def selected_identity(*, home: Path | None = None) -> AlternateIdentity | None:
    """Read launcher context from trusted shim variables, if present."""
    kind = os.environ.get(LAUNCHER_KIND_ENV)
    identifier = os.environ.get(LAUNCHER_ID_ENV)
    if kind is None and identifier is None:
        return None
    if kind not in ("numbered", "hotfix") or identifier is None:
        raise ValueError("launcher context is incomplete")
    return identity(cast("LauncherKind", kind), identifier, home=home)


def _child_text(result: ProcessResult) -> str:
    value = result.stdout
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _run(
    executable: str | Path,
    args: Sequence[str],
    *,
    env: Mapping[str, str],
    cwd: Path | None = None,
    step_id: str,
    timeout: float = 300.0,
) -> str:
    return _child_text(
        run_captured(
            str(executable),
            tuple(args),
            cwd=cwd,
            env=env,
            timeout=timeout,
            step_id=step_id,
            mutating=step_id != "alternate-selector-probe",
        )
    )


def _interpreter(executable: Path) -> Path:
    try:
        first_line = executable.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError) as exc:
        raise AlternateToolError(f"cannot inspect installed executable: {executable}") from exc
    if not first_line.startswith("#!"):
        raise AlternateToolError("installed odcli executable has no absolute interpreter")
    interpreter = Path(first_line[2:].strip().split()[0])
    if not interpreter.is_absolute() or not interpreter.is_file():
        raise AlternateToolError("installed odcli executable has no usable absolute interpreter")
    return interpreter


def _provenance(interpreter: Path, env: Mapping[str, str]) -> dict[str, str]:
    code = (
        "import importlib.metadata,json; "
        "d=importlib.metadata.distribution('odoo-instance-sdk'); "
        "print(d.read_text('direct_url.json') or '')"
    )
    raw = _run(interpreter, ("-c", code), env=env, step_id="alternate-provenance")
    try:
        payload = json.loads(raw.strip())
    except json.JSONDecodeError as exc:
        raise AlternateToolError("installed package has no readable VCS provenance") from exc
    if not isinstance(payload, dict):
        raise AlternateToolError("installed package has invalid VCS provenance")
    url = payload.get("url")
    vcs = payload.get("vcs_info")
    commit = vcs.get("commit_id") if isinstance(vcs, dict) else None
    if not isinstance(url, str) or not isinstance(commit, str):
        raise AlternateToolError("installed package provenance is incomplete")
    source = url.removeprefix("git+").removesuffix(".git")
    if "@" in source:
        source = source.split("@", 1)[0]
    if source.removesuffix(".git") != REPOSITORY_URL.removesuffix(".git"):
        raise AlternateToolError("installed package came from an unsupported repository")
    if commit != commit.lower() or not SHA_RE.fullmatch(commit):
        raise AlternateToolError("installed package provenance has an invalid commit")
    return {"repository": REPOSITORY_URL, "sha": commit}


def _probe(identity_value: AlternateIdentity, interpreter: Path, env: Mapping[str, str]) -> None:
    with tempfile.TemporaryDirectory(prefix="odcli-selector-") as temporary:
        probe_home = Path(temporary)
        canonical = probe_home / ".odcli"
        canonical.mkdir(mode=0o700)
        sentinel = canonical / "sentinel"
        sentinel.write_text("canonical", encoding="utf-8")
        selected = probe_home / ".selected"
        probe_env = dict(env)
        probe_env.update(
            {
                "HOME": str(probe_home),
                USER_ROOT_ENV: str(selected),
                LAUNCHER_KIND_ENV: identity_value.kind,
                LAUNCHER_ID_ENV: identity_value.identifier,
            }
        )
        code = (
            "import os; from pathlib import Path; "
            "from odoo_instance_sdk.internal.paths import get_user_root; "
            "expected=Path(os.environ['ODCLI_USER_ROOT']).resolve(); "
            "actual=get_user_root().resolve(); "
            "assert actual == expected, (actual, expected)"
        )
        _run(interpreter, ("-c", code), env=probe_env, step_id="alternate-selector-probe")
        if sentinel.read_text(encoding="utf-8") != "canonical":
            raise AlternateToolError("selector probe changed canonical state")
        if selected == identity_value.state_root:
            raise AlternateToolError("selector probe used the manager identity root")


def render_launcher(manifest: AlternateManifest) -> str:
    """Render the sole launcher implementation used by both alternate kinds."""
    identity_value = manifest.identity
    warning = (
        f"    print('WARNING: {identity_value.launcher_name} is experimental ({manifest.sha})', file=sys.stderr)\n"
        if identity_value.kind == "hotfix"
        else ""
    )
    return (
        "#!/usr/bin/env python3\n"
        "import fcntl, os, subprocess, sys\n"
        f"with open({str(identity_value.lock)!r}, 'a', encoding='utf-8') as lock:\n"
        "    fcntl.flock(lock, fcntl.LOCK_SH)\n"
        f"    os.environ[{USER_ROOT_ENV!r}] = {str(identity_value.state_root)!r}\n"
        f"    os.environ[{LAUNCHER_KIND_ENV!r}] = {identity_value.kind!r}\n"
        f"    os.environ[{LAUNCHER_ID_ENV!r}] = {identity_value.identifier!r}\n"
        f"{warning}"
        f"    raise SystemExit(subprocess.call([{str(manifest.executable)!r}, *sys.argv[1:]]))\n"
    )


def _atomic_text(path: Path, content: str, *, mode: int) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as stream:
        temporary = Path(stream.name)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def _safe_existing(path: Path, *, directory: bool = False) -> bool:
    if path.is_symlink():
        raise AlternateToolError(f"refusing to use symlink: {path}")
    if not path.exists():
        return False
    if directory and not path.is_dir():
        raise AlternateToolError(f"expected directory: {path}")
    return True


def _reject_symlink_components(path: Path, *, stop: Path) -> None:
    """Reject symlinked deterministic parents before creating or replacing state."""
    current = path
    stop = stop.resolve()
    while True:
        if current.is_symlink():
            raise AlternateToolError(f"refusing to use symlink: {current}")
        if current == stop or current.parent == current:
            return
        current = current.parent


def _check_install_target(identity_value: AlternateIdentity, *, replace: bool) -> None:
    """Recheck all install policy and containment facts at the publication boundary."""
    _reject_symlink_components(identity_value.manager_root.parent, stop=identity_value.home)
    for target in (
        identity_value.manager_root,
        identity_value.manifest,
        identity_value.lock,
        identity_value.launcher,
        identity_value.tool_root,
        identity_value.tool_bin,
        identity_value.state_root,
    ):
        if target.is_symlink():
            raise AlternateToolError(f"refusing to replace symlink: {target}")
        if not _under(target, identity_value.home):
            raise AlternateToolError(f"alternate target escapes home: {target}")
    existing = any(
        target.exists()
        for target in (
            identity_value.manifest,
            identity_value.launcher,
            identity_value.tool_root,
            identity_value.tool_bin,
            identity_value.state_root,
        )
    )
    if existing and not replace:
        raise AlternateToolError(f"{identity_value.launcher_name} already exists")
    if identity_value.kind == "hotfix" and replace:
        raise AlternateToolError("hot-fix replacement is not supported")
    if replace:
        if not identity_value.manifest.is_file() or identity_value.manifest.is_symlink():
            raise AlternateToolError("cannot replace an unverified alternate manifest")
        previous = _read_manifest(identity_value.manifest, home=identity_value.home)
        if previous.identity != identity_value:
            raise AlternateToolError("cannot replace an identity-mismatched alternate")
        _verify_structure(previous)


def _under(path: Path, root: Path) -> bool:
    try:
        return path.resolve(strict=False).is_relative_to(root.resolve())
    except OSError:
        return False


def _manifest_from_payload(  # noqa: C901 -- strict discriminated manifest parsing
    payload: Mapping[str, str | int | None], *, home: Path | None = None
) -> AlternateManifest:
    expected = {
        "version",
        "kind",
        "identifier",
        "sha",
        "repository",
        "state_root",
        "manager_root",
        "tool_root",
        "tool_bin",
        "executable",
        "launcher",
        "manifest",
        "lock",
        "shim",
        "pr",
        "review_sha",
        "review_session",
        "branch",
    }
    kind = payload.get("kind")
    if kind == "numbered":
        linkage_fields = {
            "checkout_path",
            "checkout_repository",
            "checkout_sha",
            "environment_id",
            "environment_path",
            "environment_lock",
            "interpreter_path",
            "hash_lock_sha256",
        }
        if linkage_fields <= set(payload):
            expected |= linkage_fields
        elif set(payload) & linkage_fields:
            raise AlternateToolError("incomplete numbered checkout linkage")
    if set(payload) != expected or payload.get("version") != MANIFEST_VERSION:
        raise AlternateToolError("unsupported or incomplete alternate-tool manifest")
    identifier = payload.get("identifier")
    if kind not in ("numbered", "hotfix") or not isinstance(identifier, str):
        raise AlternateToolError("invalid alternate-tool identity")
    identity_value = identity(cast("LauncherKind", kind), identifier, home=home)
    paths = {
        "state_root": identity_value.state_root,
        "manager_root": identity_value.manager_root,
        "tool_root": identity_value.tool_root,
        "tool_bin": identity_value.tool_bin,
        "executable": identity_value.executable,
        "launcher": identity_value.launcher,
        "manifest": identity_value.manifest,
        "lock": identity_value.lock,
    }
    for field, expected_path in paths.items():
        value = payload.get(field)
        if not isinstance(value, str) or Path(value).expanduser().resolve() != expected_path:
            raise AlternateToolError(f"manifest identity mismatch in {field}")
    sha = payload.get("sha")
    repository = payload.get("repository")
    executable = payload.get("executable")
    shim = payload.get("shim")
    if not isinstance(sha, str) or not isinstance(repository, str):
        raise AlternateToolError("invalid alternate-tool manifest fields")
    if not isinstance(executable, str) or not isinstance(shim, str):
        raise AlternateToolError("invalid alternate-tool manifest fields")
    pr = payload.get("pr")
    review_sha = payload.get("review_sha")
    review_session = payload.get("review_session")
    branch = payload.get("branch")
    if not (pr is None or isinstance(pr, int)):
        raise AlternateToolError("invalid hot-fix manifest fields")
    if not all(
        value is None or isinstance(value, str) for value in (review_sha, review_session, branch)
    ):
        raise AlternateToolError("invalid hot-fix manifest fields")
    review_sha_value = cast("str | None", review_sha)
    review_session_value = cast("str | None", review_session)
    branch_value = cast("str | None", branch)
    linkage = {
        field: payload.get(field)
        for field in (
            "checkout_path",
            "checkout_repository",
            "checkout_sha",
            "environment_id",
            "environment_path",
            "environment_lock",
            "interpreter_path",
            "hash_lock_sha256",
        )
    }
    if not all(value is None or isinstance(value, str) for value in linkage.values()):
        raise AlternateToolError("invalid numbered checkout linkage")
    return AlternateManifest(
        identity_value,
        sha,
        repository,
        Path(executable),
        shim,
        pr=pr,
        review_sha=review_sha_value,
        review_session=review_session_value,
        branch=branch_value,
        **cast("dict[str, str | None]", linkage),
    )


def _read_manifest(path: Path, *, home: Path | None = None) -> AlternateManifest:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AlternateToolError(f"cannot read alternate-tool manifest: {path}") from exc
    if not isinstance(payload, dict):
        raise AlternateToolError("alternate-tool manifest is not an object")
    return _manifest_from_payload(payload, home=home)


def read_alternate_manifest(
    identity_value: AlternateIdentity, *, strict: bool = True
) -> AlternateManifest | None:
    """Read one verified manifest for private policy adapters."""
    try:
        manifest = _read_manifest(identity_value.manifest, home=identity_value.home)
        if manifest.identity != identity_value:
            raise AlternateToolError("alternate manifest identity mismatch")  # noqa: TRY301
        _verify_structure(manifest)
    except AlternateToolError:
        if strict:
            raise
        return None
    return manifest


def publish_alternate_linkage(
    identity_value: AlternateIdentity,
    *,
    checkout_path: str,
    checkout_repository: str,
    checkout_sha: str,
    environment_id: str,
    environment_path: str,
    environment_lock: str,
    interpreter_path: str,
    hash_lock_sha256: str,
) -> AlternateManifest:
    """Publish the fixed numbered checkout/environment linkage under its lock."""
    if identity_value.kind != "numbered":
        raise AlternateToolError("only numbered identities can publish checkout linkage")
    with lifecycle_lock(identity_value, exclusive=True):
        current = read_alternate_manifest(identity_value)
        assert current is not None
        linked = replace(
            current,
            checkout_path=checkout_path,
            checkout_repository=checkout_repository,
            checkout_sha=checkout_sha,
            environment_id=environment_id,
            environment_path=environment_path,
            environment_lock=environment_lock,
            interpreter_path=interpreter_path,
            hash_lock_sha256=hash_lock_sha256,
        )
        _atomic_text(
            identity_value.manifest,
            json.dumps(linked.to_json(), indent=2, sort_keys=True) + "\n",
            mode=0o600,
        )
        return linked


def _verify_structure(manifest: AlternateManifest) -> None:
    identity_value = manifest.identity
    if manifest.executable != identity_value.executable:
        raise AlternateToolError("manifest executable identity mismatch")
    if not _under(identity_value.tool_root, identity_value.manager_root) or not _under(
        identity_value.tool_bin, identity_value.manager_root
    ):
        raise AlternateToolError("alternate tool path escapes its manager root")
    if identity_value.launcher.is_symlink() or not identity_value.launcher.is_file():
        raise AlternateToolError("alternate launcher is missing or a symlink")
    if identity_value.manifest.is_symlink() or identity_value.lock.is_symlink():
        raise AlternateToolError("alternate metadata is a symlink")
    if identity_value.launcher.read_text(encoding="utf-8") != manifest.shim:
        raise AlternateToolError("alternate launcher was modified")
    if not identity_value.executable.is_file() or identity_value.executable.is_symlink():
        raise AlternateToolError("alternate executable is missing or a symlink")
    _safe_existing(identity_value.tool_root, directory=True)
    _safe_existing(identity_value.tool_bin, directory=True)
    if identity_value.state_root.exists() and identity_value.state_root.is_symlink():
        raise AlternateToolError("alternate state root is a symlink")


@contextlib.contextmanager
def lifecycle_lock(identity_value: AlternateIdentity, *, exclusive: bool) -> Iterator[int]:
    """Acquire the same lock used by rendered launchers for a full operation."""
    _reject_symlink_components(identity_value.manager_root.parent, stop=identity_value.home)
    if identity_value.manager_root.is_symlink() or identity_value.lock.is_symlink():
        raise AlternateToolError(f"refusing to use symlink: {identity_value.lock}")
    identity_value.lock.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        fd = os.open(
            identity_value.lock,
            os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except OSError as exc:
        raise AlternateToolError(
            f"cannot safely open lifecycle lock: {identity_value.lock}"
        ) from exc
    try:
        mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        try:
            fcntl.flock(fd, mode | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise AlternateToolError(
                f"alternate launcher is still running: {identity_value.launcher_name}"
            ) from exc
        yield fd
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        with contextlib.suppress(OSError):
            os.close(fd)


def install_alternate_tool(  # noqa: C901
    identity_value: AlternateIdentity,
    sha: str,
    *,
    replace: bool = False,
    pr: int | None = None,
    review_sha: str | None = None,
    review_session: str | None = None,
    branch: str | None = None,
    checkout_path: str | None = None,
    checkout_repository: str | None = None,
    checkout_sha: str | None = None,
    environment_id: str | None = None,
    environment_path: str | None = None,
    environment_lock: str | None = None,
    interpreter_path: str | None = None,
    hash_lock_sha256: str | None = None,
    repository: str = REPOSITORY_URL,
) -> AlternateManifest:
    """Install, verify, probe, and publish one alternate launcher atomically."""
    if not SHA_RE.fullmatch(sha):
        raise ValueError("revision must be a complete lowercase SHA")
    if repository != REPOSITORY_URL:
        raise ValueError("unsupported source repository")
    _check_install_target(identity_value, replace=replace)
    manager_parent = identity_value.manager_root.parent
    manager_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{identity_value.identifier}.", dir=manager_parent))
    stage_tool = stage / "tool"
    stage_bin = stage / "bin"
    environment = dict(os.environ)
    environment.update({"UV_TOOL_DIR": str(stage_tool), "UV_TOOL_BIN_DIR": str(stage_bin)})
    try:
        _run(
            "uv",
            ("tool", "install", f"odoo-instance-sdk @ git+{REPOSITORY_URL}@{sha}"),
            env=environment,
            step_id="alternate-tool-install",
        )
        executable = stage_bin / "odcli"
        if not executable.is_file() or executable.is_symlink():
            raise AlternateToolError("uv completed without publishing an odcli executable")
        interpreter = _interpreter(executable)
        provenance = _provenance(interpreter, environment)
        if provenance != {"repository": REPOSITORY_URL, "sha": sha}:
            raise AlternateToolError("installed package provenance does not match requested SHA")
        _probe(identity_value, interpreter, environment)
        shim = render_launcher(
            AlternateManifest(
                identity_value,
                sha,
                REPOSITORY_URL,
                identity_value.executable,
                "",
                pr=pr,
                review_sha=review_sha,
                review_session=review_session,
                branch=branch,
                checkout_path=checkout_path,
                checkout_repository=checkout_repository,
                checkout_sha=checkout_sha,
                environment_id=environment_id,
                environment_path=environment_path,
                environment_lock=environment_lock,
                interpreter_path=interpreter_path,
                hash_lock_sha256=hash_lock_sha256,
            )
        )
        manifest = AlternateManifest(
            identity_value,
            sha,
            REPOSITORY_URL,
            identity_value.executable,
            shim,
            pr=pr,
            review_sha=review_sha,
            review_session=review_session,
            branch=branch,
            checkout_path=checkout_path,
            checkout_repository=checkout_repository,
            checkout_sha=checkout_sha,
            environment_id=environment_id,
            environment_path=environment_path,
            environment_lock=environment_lock,
            interpreter_path=interpreter_path,
            hash_lock_sha256=hash_lock_sha256,
        )
        with lifecycle_lock(identity_value, exclusive=True):
            _check_install_target(identity_value, replace=replace)
            identity_value.manager_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            backup = (
                identity_value.manager_root.parent
                / f".{identity_value.identifier}.backup-{uuid.uuid4().hex}"
            )
            backup.mkdir(mode=0o700)
            old_tool = backup / "tool"
            old_bin = backup / "bin"
            old_launcher = backup / "launcher"
            old_manifest = backup / "manifest.json"
            if identity_value.tool_root.exists():
                os.replace(identity_value.tool_root, old_tool)
            if identity_value.tool_bin.exists():
                os.replace(identity_value.tool_bin, old_bin)
            if identity_value.launcher.exists():
                os.replace(identity_value.launcher, old_launcher)
            if identity_value.manifest.exists():
                os.replace(identity_value.manifest, old_manifest)
            try:
                os.replace(stage_tool, identity_value.tool_root)
                os.replace(stage_bin, identity_value.tool_bin)
                _atomic_text(identity_value.launcher, shim, mode=0o700)
                _atomic_text(
                    identity_value.manifest,
                    json.dumps(manifest.to_json(), indent=2, sort_keys=True) + "\n",
                    mode=0o600,
                )
            except BaseException:
                with contextlib.suppress(OSError):
                    shutil.rmtree(identity_value.tool_root)
                with contextlib.suppress(OSError):
                    shutil.rmtree(identity_value.tool_bin)
                with contextlib.suppress(FileNotFoundError):
                    identity_value.launcher.unlink()
                with contextlib.suppress(FileNotFoundError):
                    identity_value.manifest.unlink()
                if old_tool.exists():
                    os.replace(old_tool, identity_value.tool_root)
                if old_bin.exists():
                    os.replace(old_bin, identity_value.tool_bin)
                if old_launcher.exists():
                    os.replace(old_launcher, identity_value.launcher)
                if old_manifest.exists():
                    os.replace(old_manifest, identity_value.manifest)
                raise
            else:
                if backup.exists():
                    shutil.rmtree(backup)
        return manifest
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def inspect_alternate_tool(
    identity_value: AlternateIdentity, *, strict: bool = True
) -> dict[str, str | int | bool | None]:
    """Return a bounded structural projection without executing installed code."""
    try:
        manifest = _read_manifest(identity_value.manifest, home=identity_value.home)
        if manifest.identity != identity_value:
            raise AlternateToolError("alternate manifest identity mismatch")  # noqa: TRY301
        _verify_structure(manifest)
    except AlternateToolError:
        if strict:
            raise
        return {
            "kind": identity_value.kind,
            "identifier": identity_value.identifier,
            "healthy": False,
        }
    return {
        "kind": identity_value.kind,
        "identifier": identity_value.identifier,
        "sha": manifest.sha,
        "repository": manifest.repository,
        "launcher": str(identity_value.launcher),
        "tool": str(identity_value.tool_root),
        "state": str(identity_value.state_root),
        "healthy": True,
        "pr": manifest.pr,
        "review_sha": manifest.review_sha,
        "branch": manifest.branch,
        "checkout_path": manifest.checkout_path,
        "checkout_repository": manifest.checkout_repository,
        "checkout_sha": manifest.checkout_sha,
        "environment_id": manifest.environment_id,
        "environment_path": manifest.environment_path,
        "environment_lock": manifest.environment_lock,
        "interpreter_path": manifest.interpreter_path,
        "hash_lock_sha256": manifest.hash_lock_sha256,
    }


def list_alternate_tools(
    kind: LauncherKind, *, home: Path | None = None, limit: int = 100
) -> tuple[dict[str, str | int | bool | None], ...]:
    """Inspect only the deterministic manager directory, with a hard bound."""
    if limit <= 0:
        raise ValueError("list limit must be positive")
    home_path = (home or Path.home()).expanduser().resolve()
    prefix = "odcli-slots" if kind == "numbered" else "odcli-fix-tools"
    root = home_path / ".local" / "share" / prefix
    if not root.is_dir() or root.is_symlink():
        return ()
    result: list[dict[str, str | int | bool | None]] = []
    for child in sorted(root.iterdir(), key=lambda path: path.name)[:limit]:
        if child.is_symlink() or not child.is_dir() or not IDENTITY_RE.fullmatch(child.name):
            continue
        result.append(
            inspect_alternate_tool(identity(kind, child.name, home=home_path), strict=False)
        )
    return tuple(result)


def remove_alternate_tool(  # noqa: C901 -- fixed selective cleanup sequence
    identity_value: AlternateIdentity, *, _locked: bool = False
) -> None:
    """Remove only a verified, idle identity and its deterministic artifacts."""
    manifest = _read_manifest(identity_value.manifest, home=identity_value.home)
    if manifest.identity != identity_value:
        raise AlternateToolError("alternate manifest identity mismatch")
    _verify_structure(manifest)

    def remove_locked() -> None:
        locked_manifest = _read_manifest(identity_value.manifest, home=identity_value.home)
        if locked_manifest.identity != identity_value:
            raise AlternateToolError("alternate manifest identity mismatch")
        _verify_structure(locked_manifest)
        if identity_value.state_root.is_symlink():
            raise AlternateToolError("alternate state root is a symlink")
        for directory in (identity_value.tool_root, identity_value.tool_bin):
            if directory.exists() and not directory.is_dir():
                raise AlternateToolError(f"alternate tool target is not a directory: {directory}")
        if identity_value.state_root.exists():
            shutil.rmtree(identity_value.state_root)
        identity_value.launcher.unlink()
        identity_value.manifest.unlink()
        for directory in (identity_value.tool_root, identity_value.tool_bin):
            if directory.exists():
                shutil.rmtree(directory)
        with contextlib.suppress(OSError):
            identity_value.manager_root.rmdir()
        with contextlib.suppress(FileNotFoundError):
            identity_value.lock.unlink()

    if _locked:
        remove_locked()
    else:
        with lifecycle_lock(identity_value, exclusive=True):
            remove_locked()


def _cli_manifest(args: argparse.Namespace) -> AlternateManifest:
    return install_alternate_tool(
        identity(args.kind, args.identifier),
        args.sha,
        replace=args.replace,
        pr=args.pr,
        review_sha=args.review_sha,
        review_session=args.review_session,
        branch=args.branch,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    install = commands.add_parser("install")
    install.add_argument("--kind", choices=("numbered", "hotfix"), required=True)
    install.add_argument("--identifier", required=True)
    install.add_argument("--sha", required=True)
    install.add_argument("--replace", action="store_true")
    install.add_argument("--pr", type=int)
    install.add_argument("--review-sha")
    install.add_argument("--review-session")
    install.add_argument("--branch")
    for name in ("list", "inspect", "remove"):
        command = commands.add_parser(name)
        if name == "list":
            command.add_argument("--kind", choices=("numbered", "hotfix"), required=True)
        else:
            command.add_argument("--kind", choices=("numbered", "hotfix"), required=True)
            command.add_argument("--identifier", required=True)
    commands.add_parser("provenance")
    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            print(json.dumps(_cli_manifest(args).to_json(), sort_keys=True))
        elif args.command == "list":
            print(json.dumps(list_alternate_tools(args.kind), sort_keys=True))
        elif args.command == "inspect":
            print(
                json.dumps(
                    inspect_alternate_tool(identity(args.kind, args.identifier)), sort_keys=True
                )
            )
        elif args.command == "provenance":
            print(json.dumps(_provenance(Path(sys.executable), dict(os.environ)), sort_keys=True))
        else:
            remove_alternate_tool(identity(args.kind, args.identifier))
            print(json.dumps({"removed": True, "kind": args.kind, "identifier": args.identifier}))
    except (AlternateToolError, OSError, ValueError, TypeError) as exc:
        parser.exit(1, f"alternate-tool: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
