"""``uvxl``: run a package with the lock linked from its metadata.

1. Pick the root wheel and read its METADATA (``uvxl_py.root``, F7).
2. Find the ``Pylock`` URL (``uvxl_py.pylock_url``, M1 to M5, N1).
3. Fetch the lock, without credentials (``uvxl_py.fetch``, F4 to F6).
4. Verify and clean it for this root (``uvxl_py.clean``, C1 to C7).
5. Install the cleaned lock into a cached environment, with a receipt
   (``uvxl_py.environments``, C8, P1).
"""

from collections.abc import Sequence
from dataclasses import dataclass

from uvxl_py.clean import clean_root, render
from uvxl_py.environments import LockedEnvironment, ensure_environment
from uvxl_py.fetch import FetchedLock, fetch_lock
from uvxl_py.pylock_url import find_lock_url
from uvxl_py.root import RootRequest, RootWheel, select_root
from uvxl_py.uv import Uv


@dataclass(frozen=True, slots=True)
class Prepared:
    root: RootWheel
    fetched: FetchedLock
    environment: LockedEnvironment


def prepare(
    uv: Uv,
    request: RootRequest,
    insecure_hosts: Sequence[str] = (),
) -> Prepared:
    """Build (or reuse) the locked environment for ``request``."""
    root = select_root(uv, request)
    lock_url = find_lock_url(root.metadata, insecure_hosts)
    fetched = fetch_lock(lock_url, insecure_hosts)
    result = clean_root(
        root, lock_url.file_name, fetched.content, uv, tuple(sorted(request.extras))
    )
    environment = ensure_environment(uv, render(result), fetched, root)
    return Prepared(root, fetched, environment)
