"""Fetching the lock (F3 to F6).

Credentials (F3) are sent only when a configured index covers the lock URL:
same origin, and the lock's path lies under the index's base path. The base
path is the index URL without a trailing ``simple`` or ``+simple`` segment,
so ``https://pypi.local/user/index/+simple/`` covers
``https://pypi.local/user/index/+f/<hash>/pylock.toml`` (devpi).
Sources, in order: user info of that index URL, ``uv auth token`` (uv's own
credential store), the netrc entry of the host. Nothing else is ever sent.
"""

import base64
import hashlib
import netrc
import os
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path
from typing import IO, Final, override
from urllib.parse import unquote, urljoin, urlsplit

from uvlx.errors import FetchError, LockRejected
from uvlx.pylock_url import LockUrl
from uvlx.urls import origin
from uvlx.uv import Uv

REDIRECT_CODES: Final = frozenset((301, 302, 303, 307, 308))
INDEX_LEAF_SEGMENTS: Final = frozenset(("simple", "+simple"))
USER_AGENT: Final = "uvlx/0.1"


@dataclass(frozen=True, slots=True)
class FetchPolicy:
    """Limits of rule F4. ``allow_http`` exists for local tests only."""

    max_size: int = 1024 * 1024
    timeout: float = 30.0
    max_redirects: int = 5
    allow_http: bool = False


@dataclass(frozen=True, slots=True)
class Credentials:
    username: str
    password: str

    def authorization(self) -> str:
        pair = f"{self.username}:{self.password}".encode()
        return "Basic " + base64.b64encode(pair).decode()


@dataclass(frozen=True, slots=True)
class FetchedLock:
    lock_url: LockUrl
    final_url: str
    content: bytes
    sha256: str
    fetched_at: str


def index_base(index_url: str) -> str:
    """The path an index's credentials cover (without ``/simple``)."""
    path = urlsplit(index_url).path.rstrip("/")
    head, _, leaf = path.rpartition("/")
    if leaf in INDEX_LEAF_SEGMENTS:
        return head
    return path


def covering_index(url: str, configured: Sequence[str]) -> str | None:
    """The configured index whose credentials may be sent to ``url`` (F3)."""
    path = urlsplit(url).path
    for index in configured:
        if origin(index) != origin(url):
            continue
        base = index_base(index)
        if not base or path == base or path.startswith(base + "/"):
            return index
    return None


def netrc_credentials(host: str) -> Credentials | None:
    location = os.environ.get("NETRC")
    if location is None:
        path = Path.home() / ".netrc"
    else:
        path = Path(location)
    if not path.is_file():
        return None
    try:
        entry = netrc.netrc(str(path)).authenticators(host)
    except (OSError, netrc.NetrcParseError):
        return None
    if entry is None:
        return None
    login, _account, password = entry
    return Credentials(login, password)


def credentials_for(url: str, configured: Sequence[str], uv: Uv) -> Credentials | None:
    """Rule F3: credentials for the lock URL, only from a covering index."""
    index = covering_index(url, configured)
    if index is None:
        return None
    parts = urlsplit(index)
    username: str | None = None
    if parts.username:
        username = unquote(parts.username)
    if username is not None and parts.password is not None:
        return Credentials(username, unquote(parts.password))
    token = uv.auth_token(url, username)
    if token is not None:
        if username is None:
            # `uv auth login --token` stores the token under this user name.
            return Credentials("__token__", token)
        return Credentials(username, token)
    return netrc_credentials(parts.hostname or "")


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
