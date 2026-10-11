"""Descriptor-bound recursive cleanup for an owned checkout."""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.internal.alternate_tool import AlternateIdentity, AlternateManifest
    from odoo_instance_sdk.project import ProjectConfig
    from odoo_instance_sdk.resources.environment import EnvironmentCheckoutOptions

type CheckoutIdentity = tuple[int, int]
type QuarantinedCheckout = tuple[int, str, CheckoutIdentity]
DELETION_ROOT = ".odcli-deleting"


class BoundCleanupError(RuntimeError):
    """Cleanup could not prove that the owned directory remained bound."""


class _CreateOptions(Protocol):
    @property
    def client(self) -> OdooClient: ...

    @property
    def project(self) -> ProjectConfig | Path: ...

    @property
    def environment_options(self) -> EnvironmentCheckoutOptions: ...

    @property
    def reference_sha(self) -> str: ...

    @property
    def sdk_sha(self) -> str: ...


@dataclass(frozen=True, slots=True)
class _CreateCommandPlan:
    options: _CreateOptions
    home: Path
    reference: Path
    checkout_parent: Path
    repository: str

    def redacted(self) -> dict[str, str]:
        return {
            "operation": "numbered.create",
            "reference": str(self.reference),
            "checkout_parent": str(self.checkout_parent),
            "repository": self.repository,
            "reference_sha": self.options.reference_sha,
            "sdk_sha": self.options.sdk_sha,
        }


@dataclass(frozen=True, slots=True)
class _RemoveCommandPlan:
    home: Path
    number: str
    identity: AlternateIdentity
    checkout_parent: Path | None
    manifest: AlternateManifest

    def redacted(self) -> dict[str, str]:
        return {
            "operation": "numbered.remove",
            "number": self.number,
            "home": str(self.home),
            "checkout": self.manifest.checkout_path or "",
            "checkout_sha": self.manifest.checkout_sha or "",
            "environment_id": self.manifest.environment_id or "",
        }


def validate_recovery_payload(  # noqa: C901 -- exact recovery evidence schema
    payload: dict[str, str | int],
    *,
    fields: frozenset[str],
    number: str,
    canonical_repository: Callable[[str], str],
    sha_re: re.Pattern[str],
    hash_re: re.Pattern[str],
) -> dict[str, str | int]:
    if not isinstance(payload, dict) or set(payload) != fields:
        raise BoundCleanupError("numbered recovery reservation schema drifted")
    typed = payload
    if (
        not isinstance(typed["version"], int)
        or isinstance(typed["version"], bool)
        or typed["version"] != 1
        or typed["kind"] != "numbered"
        or typed["identifier"] != number
    ):
        raise BoundCleanupError("numbered recovery reservation identity drifted")
    checkout = typed["checkout"]
    repository = typed["repository"]
    if not isinstance(checkout, str) or not Path(str(checkout)).is_absolute():
        raise BoundCleanupError("numbered recovery checkout path is not absolute")
    if not isinstance(repository, str) or canonical_repository(repository) != repository:
        raise BoundCleanupError("numbered recovery repository identity is not canonical")
    for name in ("checkout_sha", "reference_sha", "sdk_sha"):
        if not isinstance(typed[name], str) or not sha_re.fullmatch(str(typed[name])):
            raise BoundCleanupError("numbered recovery SHA evidence is invalid")
    if typed["checkout_sha"] != typed["reference_sha"]:
        raise BoundCleanupError("numbered recovery checkout SHA evidence drifted")
    if not isinstance(typed["environment_id"], str) or not typed["environment_id"]:
        raise BoundCleanupError("numbered recovery environment identity is invalid")
    for name in ("environment_path", "environment_lock", "interpreter_path"):
        if not isinstance(typed[name], str) or not Path(str(typed[name])).is_absolute():
            raise BoundCleanupError("numbered recovery path evidence is invalid")
    if not isinstance(typed["hash_lock_sha256"], str) or not hash_re.fullmatch(
        str(typed["hash_lock_sha256"])
    ):
        raise BoundCleanupError("numbered recovery lock digest is invalid")
    quarantine = typed["quarantine"]
    quarantine_identity = typed["quarantine_identity"]
    if not isinstance(quarantine, str) or not isinstance(quarantine_identity, str):
        raise BoundCleanupError("numbered recovery quarantine evidence is invalid")
    if bool(quarantine) != bool(quarantine_identity) or (
        quarantine and not Path(quarantine).is_absolute()
    ):
        raise BoundCleanupError("numbered recovery quarantine evidence is invalid")
    if quarantine_identity and (
        len(quarantine_identity.split(":")) != 2
        or not all(part.isdigit() for part in quarantine_identity.split(":"))
    ):
        raise BoundCleanupError("numbered recovery quarantine identity is invalid")
    return typed


def read_recovery_reservation(
    reservation: Path,
    *,
    number: str,
    home: Path,
    fields: frozenset[str],
    canonical_repository: Callable[[str], str],
    sha_re: re.Pattern[str],
    hash_re: re.Pattern[str],
) -> dict[str, str | int]:
    parent = reservation.parent
    current = parent
    stop = home.resolve()
    while True:
        if current.is_symlink():
            raise BoundCleanupError(f"refusing symlinked path: {current}")
        if current == stop or current.parent == current:
            break
        current = current.parent
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        parent_fd = os.open(parent, flags)
    except OSError as exc:
        raise BoundCleanupError("cannot safely open recovery reservation parent") from exc
    try:
        try:
            reservation_fd = os.open(
                reservation.name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
        except OSError as exc:
            raise BoundCleanupError("recovery reservation is missing or symlinked") from exc
        try:
            if not stat.S_ISREG(os.fstat(reservation_fd).st_mode):
                raise BoundCleanupError("recovery reservation is not a regular file")
            with os.fdopen(reservation_fd, encoding="utf-8") as stream:
                reservation_fd = -1
                payload = json.load(stream)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise BoundCleanupError("numbered recovery reservation is unreadable") from exc
        finally:
            if reservation_fd >= 0:
                os.close(reservation_fd)
    finally:
        os.close(parent_fd)
    return validate_recovery_payload(
        payload,
        fields=fields,
        number=number,
        canonical_repository=canonical_repository,
        sha_re=sha_re,
        hash_re=hash_re,
    )


def quarantine_directory(
    checkout: Path,
    expected: CheckoutIdentity,
    *,
    existing_quarantine: Path | None = None,
) -> QuarantinedCheckout:
    parent = checkout.parent
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        parent_fd = os.open(parent, flags)
    except OSError as exc:
        raise BoundCleanupError("cannot safely open checkout parent") from exc
    name = (
        os.path.relpath(existing_quarantine, parent)
        if existing_quarantine is not None
        else f"{DELETION_ROOT}/.{checkout.name}.deleting"
    )
    try:
        if existing_quarantine is None:
            try:
                current = os.stat(checkout.name, dir_fd=parent_fd, follow_symlinks=False)
            except OSError as exc:
                raise BoundCleanupError("linked checkout disappeared before deletion") from exc
            if (current.st_dev, current.st_ino) != expected:
                raise BoundCleanupError(  # noqa: TRY301
                    "linked checkout identity changed before deletion"
                )
            with suppress(FileExistsError):
                os.mkdir(DELETION_ROOT, mode=0o700, dir_fd=parent_fd)
            root_fd = os.open(DELETION_ROOT, flags, dir_fd=parent_fd)
            try:
                if os.fstat(root_fd).st_mode & 0o077:
                    raise BoundCleanupError("numbered deletion namespace is not private")
                child_name = name.rsplit("/", 1)[1]
                os.mkdir(child_name, mode=0o700, dir_fd=root_fd)
                namespace_fd = os.open(child_name, flags, dir_fd=root_fd)
            finally:
                os.close(root_fd)
            try:
                os.rename(checkout.name, "target", src_dir_fd=parent_fd, dst_dir_fd=namespace_fd)
                moved = os.stat("target", dir_fd=namespace_fd, follow_symlinks=False)
            finally:
                os.close(namespace_fd)
            if (moved.st_dev, moved.st_ino) != expected:
                raise BoundCleanupError(  # noqa: TRY301
                    "linked checkout identity changed during quarantine"
                )
        elif existing_quarantine.parent.parent != parent or not name.startswith(
            f"{DELETION_ROOT}/"
        ):
            raise BoundCleanupError(  # noqa: TRY301
                "numbered recovery quarantine path drifted"
            )
        return (parent_fd, name, expected)  # noqa: TRY300
    except BaseException:
        os.close(parent_fd)
        raise


def remove_bound_directory(  # noqa: C901 -- one bounded FD cleanup transaction
    parent_fd: int, quarantine_name: str, checkout_name: str, expected: CheckoutIdentity
) -> None:
    del checkout_name
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    root_fd = -1
    namespace_fd = -1
    target_fd = -1
    try:
        root_name, child_name = quarantine_name.split("/", 1)
        if root_name != DELETION_ROOT or "/" in child_name:
            raise BoundCleanupError("numbered deletion namespace path drifted")
        root_fd = os.open(root_name, flags, dir_fd=parent_fd)
        if os.fstat(root_fd).st_mode & 0o077:
            raise BoundCleanupError("numbered deletion namespace is not private")
        namespace_fd = os.open(child_name, flags, dir_fd=root_fd)
        target_fd = os.open("target", flags, dir_fd=namespace_fd)
        target = os.fstat(target_fd)
        if (target.st_dev, target.st_ino) != expected:
            raise BoundCleanupError("quarantined checkout identity changed before deletion")
        for _, directories, files, directory_fd in os.fwalk(
            ".", topdown=False, dir_fd=target_fd, follow_symlinks=False
        ):
            for name in files:
                os.unlink(name, dir_fd=directory_fd)
            for name in directories:
                os.rmdir(name, dir_fd=directory_fd)
        current = os.fstat(target_fd)
        if (current.st_dev, current.st_ino) != expected:
            raise BoundCleanupError("quarantined checkout identity changed before final deletion")
        os.rmdir("target", dir_fd=namespace_fd)
        os.rmdir(child_name, dir_fd=root_fd)
        try:
            os.stat(child_name, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        raise BoundCleanupError("quarantined checkout was replaced during deletion")
    finally:
        if target_fd >= 0:
            os.close(target_fd)
        if namespace_fd >= 0:
            os.close(namespace_fd)
        if root_fd >= 0:
            os.close(root_fd)
