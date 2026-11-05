"""Fetching the lock (F4 to F6). Which credentials to send: ``uvlx.credentials``."""

import hashlib
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import Message
from typing import IO, Final, override
from urllib.parse import urljoin, urlsplit

from uvlx.credentials import Credentials
from uvlx.errors import FetchError, LockRejected
from uvlx.pylock_url import LockUrl
from uvlx.urls import origin

REDIRECT_CODES: Final = frozenset((301, 302, 303, 307, 308))
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
    final_url: str
    content: bytes
    sha256: str
    fetched_at: str


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Redirects are followed by ``fetch_lock`` itself, to apply F3 and F4."""

    @override
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> urllib.request.Request | None:
        return None


def check_scheme(url: str, policy: FetchPolicy) -> None:
    scheme = urlsplit(url).scheme.lower()
    if scheme == "https" or (policy.allow_http and scheme == "http"):
        return
    raise FetchError(f"[F4] refusing non-https URL {url!r}")


def read_limited(response: IO[bytes], url: str, policy: FetchPolicy) -> bytes:
    content = response.read(policy.max_size + 1)
    if len(content) > policy.max_size:
        raise FetchError(
            f"[F4] the lock at {url} is larger than {policy.max_size} bytes"
        )
    return content


def download(
    url: str, credentials: Credentials | None, policy: FetchPolicy
) -> tuple[str, bytes]:
    """GET with manual redirects; credentials never leave the first origin."""
    opener = urllib.request.build_opener(_NoRedirect)
    first_origin = origin(url)
    current = url
    for _ in range(policy.max_redirects + 1):
        check_scheme(current, policy)
        if origin(current) != first_origin:
            credentials = None  # F3: dropped for good after leaving the origin
        request = urllib.request.Request(current, headers={"User-Agent": USER_AGENT})
        if credentials is not None:
            request.add_unredirected_header(
                "Authorization", credentials.authorization()
            )
        try:
            with opener.open(request, timeout=policy.timeout) as response:
                return current, read_limited(response, current, policy)
        except urllib.error.HTTPError as error:
            location = error.headers.get("Location")
            if error.code not in REDIRECT_CODES or location is None:
                raise FetchError(
                    f"[F6] fetching the lock from {current} failed: "
                    f"HTTP {error.code} {error.reason}"
                ) from error
            current = urljoin(current, location)
        except (urllib.error.URLError, OSError) as error:
            raise FetchError(
                f"[F6] fetching the lock from {current} failed: {error}"
            ) from error
    raise FetchError(f"[F4] more than {policy.max_redirects} redirects for {url}")


def verify_pinned(lock_url: LockUrl, content: bytes) -> None:
    """Rule F5: in pinned mode the content must match before it is parsed."""
    for algorithm, expected in lock_url.pinned.items():
        actual = hashlib.new(algorithm, content).hexdigest()
        if actual != expected:
            raise LockRejected(
                f"[F5] the lock at {lock_url.url} has {algorithm} {actual}, "
                f"the Pylock URL pins {expected}"
            )


def fetch_lock(
    lock_url: LockUrl, credentials: Credentials | None, policy: FetchPolicy
) -> FetchedLock:
    """Fetch and (in pinned mode) verify the lock. Any failure is fatal (F6)."""
    final_url, content = download(lock_url.url, credentials, policy)
    verify_pinned(lock_url, content)
    return FetchedLock(
        lock_url=lock_url,
        final_url=final_url,
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        fetched_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
