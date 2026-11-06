"""Exceptions. The rule ID in the message says what went wrong."""


class VerifyError(Exception):
    """Any error of this tool: uv, network, metadata, usage."""


class LockRejected(VerifyError):
    """The lock cannot be used safely (or at all) for this root."""
