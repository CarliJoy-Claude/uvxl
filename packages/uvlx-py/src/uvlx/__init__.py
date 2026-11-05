"""Install and run a Python package with the lock linked from its metadata.

Proof of concept for the ``Pylock`` Project-URL. The uv CLI is the engine.
See ``docs/spec.md`` for the rules; their IDs appear in error messages.
"""

from uvlx.clean import CleanPackage, CleanResult, CleanWheel, clean, clean_root, render
from uvlx.errors import FetchError, LockRejected, MetadataError, UvError, VerifyError
from uvlx.fetch import FetchPolicy
from uvlx.run import Prepared, prepare
from uvlx.uv import Uv

__all__ = (
    "CleanPackage",
    "CleanResult",
    "CleanWheel",
    "FetchError",
    "FetchPolicy",
    "LockRejected",
    "MetadataError",
    "Prepared",
    "Uv",
    "UvError",
    "VerifyError",
    "clean",
    "clean_root",
    "prepare",
    "render",
)
