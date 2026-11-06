"""The ``Pylock`` Project-URL in the root's core metadata (M1 to M5, N1)."""

import string
from collections.abc import Mapping
from dataclasses import dataclass
from email.parser import BytesHeaderParser
from email.policy import compat32
from types import MappingProxyType
from typing import Final
from urllib.parse import unquote, urlsplit

from uvlx.errors import LockRejected, MetadataError
from uvlx.lock import is_pylock_name
from uvlx.toml_values import SUPPORTED_HASHES

LABEL: Final = "pylock"


@dataclass(frozen=True, slots=True)
class LockUrl:
    """Where the lock is, and the pinned hashes (empty in mutable mode)."""

    url: str
    file_name: str
    pinned: Mapping[str, str]

    @property
    def mode(self) -> str:
        if self.pinned:
            return "pinned"
        return "mutable"


def normalize_label(label: str) -> str:
    """Project-URL label normalization (well-known project URLs spec)."""
    removed = string.punctuation + string.whitespace
    return "".join(char for char in label if char not in removed).lower()


def project_urls(metadata: bytes) -> tuple[tuple[str, str], ...]:
    """All ``Project-URL`` entries as (label, url), duplicates kept (M2)."""
    message = BytesHeaderParser(policy=compat32).parsebytes(metadata)
    entries: list[tuple[str, str]] = []
    for value in message.get_all("Project-URL") or ():
        label, separator, url = str(value).partition(",")
        if not separator:
            raise MetadataError(f"malformed Project-URL {str(value)!r}")
        entries.append((label.strip(), url.strip()))
    return tuple(entries)


def parse_fragment(fragment: str) -> Mapping[str, str]:
    """Rule M4: only hash parameters (pinned mode). A bad digest fails F5."""
    pinned: dict[str, str] = {}
    if not fragment:
        return MappingProxyType(pinned)
    for parameter in fragment.split("&"):
        key, _, value = parameter.partition("=")
        if key.lower() not in SUPPORTED_HASHES:
            raise LockRejected(
                f"[M4] unknown fragment parameter {parameter!r} in the Pylock URL"
            )
        pinned[key.lower()] = value.lower()
    return MappingProxyType(pinned)


def find_lock_url(
    metadata: bytes,
    *,
    allow_http: bool = False,
) -> LockUrl:
    """The root's lock URL. Missing, duplicate or invalid: fail.

    ``allow_http`` exists for tests against a local index only.
    """
    urls = tuple(url for label, url in project_urls(metadata) if is_pylock(label))
    if not urls:
        raise LockRejected(
            "[M5] the root has no Pylock Project-URL; a locked install is impossible"
        )
    if len(urls) > 1:
        raise LockRejected(f"[M2] the root declares {len(urls)} Pylock URLs")
    parts = urlsplit(urls[0])
    if allow_http:
        allowed: tuple[str, ...] = ("https", "http")
    else:
        allowed = ("https",)
    if parts.scheme.lower() not in allowed or not parts.hostname:
        raise LockRejected(f"[M3] the Pylock URL must be https: {urls[0]!r}")
    file_name = unquote(parts.path.rpartition("/")[2])
    if not is_pylock_name(file_name):
        raise LockRejected(
            f"[N1] {file_name!r} in the Pylock URL is not a valid PEP 751 file name"
        )
    return LockUrl(
        url=parts._replace(fragment="").geturl(),
        file_name=file_name,
        pinned=parse_fragment(parts.fragment),
    )


def is_pylock(label: str) -> bool:
    return normalize_label(label) == LABEL
