"""Frozen results for Codex translation and commit-description workflows."""

from __future__ import annotations

import msgspec


class ContextFile(msgspec.Struct, frozen=True, kw_only=True):
    """One Git-tracked text file selected as model context."""

    path: str
    byte_length: int


class ContextManifest(msgspec.Struct, frozen=True, kw_only=True):
    """Paths and sizes shown before a Codex request."""

    files: tuple[ContextFile, ...]
    total_bytes: int
    limit_bytes: int


class FillResult(msgspec.Struct, frozen=True, kw_only=True):
    """One catalog after a fill attempt."""

    path: str
    language: str
    translated: int
    fuzzy: int
    untranslated: int
    filled: int
    rejected: tuple[str, ...]
    validation_status: str | None
    validation_output: str | None
    context_paths: tuple[str, ...]
    context_bytes: int
    context_limit: int
    model: str


class CommitDescription(msgspec.Struct, frozen=True, kw_only=True):
    """One validated English description line."""

    description: str
    model: str
    context_paths: tuple[str, ...]
    context_bytes: int
    context_limit: int
    patch_bytes: int
    patch_truncated: bool
