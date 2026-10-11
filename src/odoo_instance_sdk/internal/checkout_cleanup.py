"""Descriptor-bound recursive cleanup for an owned checkout."""

from __future__ import annotations

import os

type CheckoutIdentity = tuple[int, int]


class BoundCleanupError(RuntimeError):
    """Cleanup could not prove that the owned directory remained bound."""


def _restore_quarantine(
    parent_fd: int, quarantine_name: str, checkout_name: str, expected: CheckoutIdentity
) -> None:
    try:
        os.stat(checkout_name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        return
    try:
        current = os.stat(quarantine_name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        return
    if (current.st_dev, current.st_ino) == expected:
        os.rename(
            quarantine_name,
            checkout_name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )


def remove_bound_directory(
    parent_fd: int, quarantine_name: str, checkout_name: str, expected: CheckoutIdentity
) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    target_fd = -1
    try:
        target_fd = os.open(quarantine_name, flags, dir_fd=parent_fd)
        target = os.fstat(target_fd)
        if (target.st_dev, target.st_ino) != expected:
            raise BoundCleanupError(  # noqa: TRY301
                "quarantined checkout identity changed before deletion"
            )
        for _, directories, files, directory_fd in os.fwalk(
            ".", topdown=False, dir_fd=target_fd, follow_symlinks=False
        ):
            for name in files:
                os.unlink(name, dir_fd=directory_fd)
            for name in directories:
                os.rmdir(name, dir_fd=directory_fd)
        current = os.stat(quarantine_name, dir_fd=parent_fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != expected:
            raise BoundCleanupError(  # noqa: TRY301
                "quarantined checkout identity changed before final deletion"
            )
        os.rmdir(quarantine_name, dir_fd=parent_fd)
        try:
            os.stat(quarantine_name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        raise BoundCleanupError("quarantined checkout was replaced during deletion")  # noqa: TRY301
    except BaseException:
        if target_fd >= 0:
            _restore_quarantine(parent_fd, quarantine_name, checkout_name, expected)
        raise
    finally:
        if target_fd >= 0:
            os.close(target_fd)
