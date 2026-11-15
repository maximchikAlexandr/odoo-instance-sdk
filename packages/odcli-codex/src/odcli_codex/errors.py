"""Errors raised by Codex workflow checks."""

from __future__ import annotations


class CodexWorkflowError(Exception):
    """A model, context, or catalog check rejected the operation."""
