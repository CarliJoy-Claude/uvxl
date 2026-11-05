"""``uvlx``: run a package with the lock linked from its metadata.

1. Pick the root wheel and read its METADATA (``uvlx.root``, F7).
2. Find the ``Pylock`` URL (``uvlx.pylock_url``, M1 to M4, N1).
3. Fetch the lock with credentials only from a covering index
   (``uvlx.fetch``, F3 to F6).
4. Verify and clean it for this root (``uvlx.clean``, C1 to C7).
5. Install the cleaned lock into a cached environment, with a receipt
   (``uvlx.environments``, C8, P1).
"""

from dataclasses import dataclass
from pathlib import Path

from uvlx.clean import CleanResult, clean_root, configured_indexes, render
from uvlx.environments import LockedEnvironment, ensure_environment
from uvlx.fetch import FetchedLock, FetchPolicy, credentials_for, fetch_lock
from uvlx.pylock_url import find_lock_url
from uvlx.root import RootRequest, select_root
from uvlx.uv import Uv
from uvlx.wheel import RootWheel


@dataclass(frozen=True, slots=True)
class Prepared:
    root: RootWheel
    fetched: FetchedLock
    result: CleanResult
    environment: LockedEnvironment


def prepare(
    uv: Uv,
    request: RootRequest,
    cache_dir: Path,
    policy: FetchPolicy,
    *,
    allow_unknown_fragment: bool = False,
) -> Prepared:
    """Build (or reuse) the locked environment for ``request``."""
    root = select_root(uv, request)
    lock_url = find_lock_url(
        root.metadata,
        allow_unknown_fragment=allow_unknown_fragment,
        allow_http=policy.allow_http,
    )
    configured = configured_indexes(uv, f"{root.name}=={root.version}")
    credentials = credentials_for(lock_url.url, configured, uv)
    fetched = fetch_lock(lock_url, credentials, policy)
    result = clean_root(
        root, lock_url.file_name, fetched.content, uv, tuple(sorted(request.extras))
    )
    environment = ensure_environment(uv, render(result), fetched, root, cache_dir)
    return Prepared(root, fetched, result, environment)
