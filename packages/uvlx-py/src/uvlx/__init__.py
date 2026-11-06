"""Install and run a Python package with the lock linked from its metadata.

Proof of concept for the ``Pylock`` Project-URL. The uv CLI is the engine.
See ``docs/spec.md`` for the rules; their IDs appear in error messages.
"""

from uvlx.clean import CleanPackage, CleanResult, clean_root, render
from uvlx.errors import LockRejected, VerifyError
from uvlx.fetch import FetchPolicy
from uvlx.run import Prepared, prepare
from uvlx.uv import Uv

__all__ = (
    "CleanPackage",
    "CleanResult",
    "FetchPolicy",
    "LockRejected",
    "Prepared",
    "Uv",
    "VerifyError",
    "clean_root",
    "prepare",
    "render",
)
