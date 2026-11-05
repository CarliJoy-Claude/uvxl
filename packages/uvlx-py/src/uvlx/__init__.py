"""Install and run a Python package with the lock linked from its metadata.

Proof of concept for the ``Pylock`` Project-URL. The uv CLI is the engine.
See ``docs/spec.md`` for the rules; their IDs appear in error messages.
"""

from uvlx.clean import CleanPackage, CleanResult, CleanWheel, clean, render
from uvlx.errors import LockRejected, MetadataError, UvError, VerifyError
from uvlx.uv import Uv

__all__ = (
    "CleanPackage",
    "CleanResult",
    "CleanWheel",
    "LockRejected",
    "MetadataError",
    "Uv",
    "UvError",
    "VerifyError",
    "clean",
    "render",
)
