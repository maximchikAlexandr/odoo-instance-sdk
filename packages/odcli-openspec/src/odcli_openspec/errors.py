"""Typed failures for the OpenSpec adapter."""


class OpenSpecError(Exception):
    """A binding or probe failed with a stable reason code.

    Args:
        reason: Machine-readable reason such as ``openspec_incompatible``.
        message: Short sanitized explanation.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
