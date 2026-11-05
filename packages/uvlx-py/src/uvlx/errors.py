"""Exceptions. The CLI maps them to exit codes (see ``uvlx.cli``)."""


class VerifyError(Exception):
    """Base class for all errors of this tool."""


class LockRejected(VerifyError):
    """The lock cannot be used safely (or at all) for this root wheel. Exit 1."""


class MetadataError(VerifyError):
    """Wheel metadata is missing or malformed."""


class UvError(VerifyError):
    """Running uv failed for a reason other than the lock. Exit 2."""
