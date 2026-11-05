"""URL identity: origins and index keys."""

from collections.abc import Sequence
from urllib.parse import urlsplit

type Origin = tuple[str, str, int]
type IndexKey = tuple[Origin, str]


def origin(url: str) -> Origin:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    if port is None:
        if scheme == "https":
            port = 443
        else:
            port = 80
    return (scheme, host, port)


def index_key(url: str) -> IndexKey:
    """Identity of an index: origin and path, ignoring user info and slashes."""
    return (origin(url), urlsplit(url).path.rstrip("/"))


def strip_user_info(url: str) -> str:
    parts = urlsplit(url)
    netloc = parts.netloc.rpartition("@")[2]
    return parts._replace(netloc=netloc).geturl().rstrip("/")


def configured_index_for(url: str, configured: Sequence[str]) -> str | None:
    """The configured index on the same origin as a file URL, if any."""
    for index in configured:
        if origin(index) == origin(url):
            return index
    return None
