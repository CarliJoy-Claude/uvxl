"""``uvxl``: run a package with the lock linked from its metadata.

1. Pick the root wheel and read its METADATA (``uvxl.root``, F7).
2. Find the ``Pylock`` URL (``uvxl.pylock_url``, M1 to M5, N1).
3. Fetch the lock, without credentials (``uvxl.fetch``, F4 to F6).
4. Verify and clean it for this root (``uvxl.clean``, C1 to C7).
5. Install the cleaned lock into a cached environment, with a receipt
   (``uvxl.environments``, C8, P1).
"""

from dataclasses import dataclass
from pathlib import Path

from uvxl.clean import clean_root, render
from uvxl.environments import LockedEnvironment, ensure_environment
from uvxl.fetch import FetchedLock, FetchPolicy, fetch_lock
from uvxl.pylock_url import find_lock_url
from uvxl.root import RootRequest, RootWheel, select_root
from uvxl.uv import Uv


@dataclass(frozen=True, slots=True)
class Prepared:
    root: RootWheel
    fetched: FetchedLock
    environment: LockedEnvironment


def prepare(
    uv: Uv,
    request: RootRequest,
    cache_dir: Path,
    policy: FetchPolicy,
) -> Prepared:
    """Build (or reuse) the locked environment for ``request``."""
    root = select_root(uv, request)
    lock_url = find_lock_url(root.metadata, allow_http=policy.allow_http)
    fetched = fetch_lock(lock_url, policy)
    result = clean_root(
        root, lock_url.file_name, fetched.content, uv, tuple(sorted(request.extras))
    )
    environment = ensure_environment(uv, render(result), fetched, root, cache_dir)
    return Prepared(root, fetched, environment)
