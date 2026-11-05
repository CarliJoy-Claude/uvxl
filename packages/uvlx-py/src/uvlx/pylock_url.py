"""The ``Pylock`` Project-URL in the root's core metadata (M1 to M4, N1)."""

import re
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

LABEL: Final = "pylock"
HASH_LENGTHS: Final = MappingProxyType({"sha256": 64, "sha384": 96, "sha512": 128})
HEX_RE: Final = re.compile(r"^[0-9a-f]+$")


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


def parse_fragment(fragment: str, allow_unknown: bool) -> Mapping[str, str]:
    """Rule M4: hash parameters pin the lock; anything else is refused."""
    pinned: dict[str, str] = {}
    if not fragment:
        return MappingProxyType(pinned)
    for parameter in fragment.split("&"):
        key, separator, value = parameter.partition("=")
        key = key.lower()
        if key not in HASH_LENGTHS:
            if allow_unknown:
                continue
            raise LockRejected(
                f"[M4] unknown fragment parameter {parameter!r} in the Pylock URL"
            )
        value = value.lower()
        valid = HEX_RE.match(value) is not None and len(value) == HASH_LENGTHS[key]
        if not separator or not valid:
            raise LockRejected(f"[M4] invalid {key} digest in the Pylock URL")
        if key in pinned:
            raise LockRejected(f"[M4] {key} given twice in the Pylock URL")
        pinned[key] = value
    return MappingProxyType(pinned)


def find_lock_url(
    metadata: bytes,
    *,
    allow_unknown_fragment: bool = False,
    allow_http: bool = False,
) -> LockUrl:
    """The root's lock URL. Missing, duplicate or invalid: fail.

    ``allow_http`` exists for tests against a local index only.
    """
    urls = tuple(url for label, url in project_urls(metadata) if is_pylock(label))
    if not urls:
        raise LockRejected(
            "[M1] the root has no Pylock Project-URL; a locked install is impossible"
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
        pinned=parse_fragment(parts.fragment, allow_unknown_fragment),
    )


def is_pylock(label: str) -> bool:
    return normalize_label(label) == LABEL
