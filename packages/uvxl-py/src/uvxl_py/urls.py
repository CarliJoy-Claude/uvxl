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


def is_insecure_host(url: str, insecure_hosts: Sequence[str]) -> bool:
    """Whether ``url`` matches one of uv's ``--allow-insecure-host`` values.

    Same syntax and matching as uv's ``TrustedHost``: ``*``, ``host``,
    ``host:port`` or ``scheme://host:port``.
    """
    parts = urlsplit(url)
    for value in insecure_hosts:
        if value == "*":
            return True
        scheme, separator, rest = value.partition("://")
        if not separator:
            scheme, rest = "", value
        host, _, port = rest.split("/", 1)[0].partition(":")
        if scheme and scheme != parts.scheme:
            continue
        if port and parts.port != int(port):
            continue
        if host == parts.hostname:
            return True
    return False
