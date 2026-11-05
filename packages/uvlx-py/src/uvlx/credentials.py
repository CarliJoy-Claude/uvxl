"""Credentials for the lock (F3): only from keyring, only for a covered URL.

A configured index covers the lock URL when both have the same origin and
the lock's path lies under the index's base path. The base path is the index
URL without a trailing ``simple`` or ``+simple`` segment, so devpi's
``https://pypi.local/user/index/+simple/`` covers
``https://pypi.local/user/index/+f/<hash>/pylock.toml``.

The lookup copies uv's subprocess keyring provider
(``crates/uv-auth/src/keyring.rs``):

- with the user name of the index URL: ``keyring get <index-url> <user>``,
  then ``keyring get <host[:port]> <user>``; stdout is the password.
- without one: ``keyring get <index-url> --mode creds``; stdout is the
  user name and the password on two lines. (uv does this only with
  ``authenticate = always``.)
"""

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final
from urllib.parse import unquote, urlsplit

from uvlx.urls import origin, strip_user_info

INDEX_LEAF_SEGMENTS: Final = frozenset(("simple", "+simple"))


@dataclass(frozen=True, slots=True)
class Credentials:
    username: str
    password: str


def index_base(index_url: str) -> str:
    """The path an index's credentials cover (without ``/simple``)."""
    path = urlsplit(index_url).path.rstrip("/")
    head, _, leaf = path.rpartition("/")
    if leaf in INDEX_LEAF_SEGMENTS:
        return head
    return path


def covering_index(url: str, configured: Sequence[str]) -> str | None:
    """The configured index whose credentials may be sent to ``url``."""
    path = urlsplit(url).path
    for index in configured:
        if origin(index) != origin(url):
            continue
        base = index_base(index)
        if not base or path == base or path.startswith(base + "/"):
            return index
    return None


def keyring_get(service: str, username: str | None) -> Credentials | None:
    """One ``keyring get`` call. Missing keyring or no entry: None."""
    if username is None:
        command: tuple[str, ...] = ("keyring", "get", service, "--mode", "creds")
    else:
        command = ("keyring", "get", service, username)
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    if username is not None:
        return Credentials(username, result.stdout.removesuffix("\n"))
    lines = result.stdout.splitlines()
    if len(lines) < 2:
        return None
    return Credentials(lines[0], lines[1])


def credentials_for(url: str, configured: Sequence[str]) -> Credentials | None:
    """Rule F3: keyring credentials of the index covering ``url``, if any."""
    index = covering_index(url, configured)
    if index is None:
        return None
    parts = urlsplit(index)
    if not parts.username:
        return keyring_get(strip_user_info(index), None)
    username = unquote(parts.username)
    found = keyring_get(strip_user_info(index), username)
    if found is None:
        host = parts.netloc.rpartition("@")[2]
        found = keyring_get(host, username)
    return found
