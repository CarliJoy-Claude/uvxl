"""Fetching the lock (F4 to F6).

The Python PoC sends no credentials at all, so F3 holds trivially and locks
must be readable without login. Credentials for privately hosted locks are
left to the Rust version, which can use uv's own credential handling.

httpx2 follows redirects up to the limit; a request hook refuses every
non-https hop (F4). The body is streamed, so the size limit holds.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

import httpx2

from uvlx.errors import LockRejected, VerifyError
from uvlx.pylock_url import LockUrl

USER_AGENT: Final = "uvlx/0.1"


@dataclass(frozen=True, slots=True)
class FetchPolicy:
    """Limits of rule F4. ``allow_http`` exists for local tests only."""

    max_size: int = 1024 * 1024
    timeout: float = 30.0
    max_redirects: int = 5
    allow_http: bool = False


@dataclass(frozen=True, slots=True)
class FetchedLock:
    lock_url: LockUrl
    content: bytes
    sha256: str
    fetched_at: str


def https_only(policy: FetchPolicy) -> Callable[[httpx2.Request], None]:
    """Request hook: every request, including redirects, must be https."""

    def check(request: httpx2.Request) -> None:
        scheme = request.url.scheme
        if scheme == "https" or (policy.allow_http and scheme == "http"):
            return
        raise VerifyError(f"[F4] refusing non-https URL {request.url}")

    return check


def download(url: str, policy: FetchPolicy) -> bytes:
    """GET ``url`` without credentials; return the body."""
    client = httpx2.Client(
        follow_redirects=True,
        max_redirects=policy.max_redirects,
        timeout=policy.timeout,
        headers={"User-Agent": USER_AGENT},
        event_hooks={"request": [https_only(policy)]},
    )
    try:
        with client, client.stream("GET", url) as response:
            response.raise_for_status()
            content = bytearray()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > policy.max_size:
                    raise VerifyError(
                        f"[F4] the lock at {url} is larger than {policy.max_size} bytes"
                    )
            return bytes(content)
    except httpx2.TooManyRedirects as error:
        raise VerifyError(f"[F4] too many redirects for {url}") from error
    except httpx2.HTTPError as error:
        raise VerifyError(
            f"[F6] fetching the lock from {url} failed: {error}"
        ) from error


def verify_pinned(lock_url: LockUrl, content: bytes) -> None:
    """Rule F5: in pinned mode the content must match before it is parsed."""
    for algorithm, expected in lock_url.pinned.items():
        actual = hashlib.new(algorithm, content).hexdigest()
        if actual != expected:
            raise LockRejected(
                f"[F5] the lock at {lock_url.url} has {algorithm} {actual}, "
                f"the Pylock URL pins {expected}"
            )


def fetch_lock(lock_url: LockUrl, policy: FetchPolicy) -> FetchedLock:
    """Fetch and (in pinned mode) verify the lock. Any failure is fatal (F6)."""
    content = download(lock_url.url, policy)
    verify_pinned(lock_url, content)
    return FetchedLock(
        lock_url=lock_url,
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        fetched_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
