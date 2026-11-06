"""Reading the candidate pylock that ``uv pip compile`` prints.

This is uv's own output, so it is read without validation. The maintainer's
lock is the untrusted input; ``uvlx.lock`` checks that one carefully.
"""

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final
from urllib.parse import unquote, urlsplit

from packaging.utils import canonicalize_name
from packaging.version import Version

from uvlx.toml_values import supported_hashes

YANKED_RE: Final = re.compile(r"`([^`=\s]+)==([^`\s]+)` is yanked")


@dataclass(frozen=True, slots=True)
class CandidateWheel:
    filename: str
    url: str
    size: int | None
    hashes: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class Candidate:
    name: str
    version: Version
    wheels: tuple[CandidateWheel, ...]


@dataclass(frozen=True, slots=True)
class CandidateLock:
    packages: tuple[Candidate, ...]
    requires_python: str | None


def parse_candidates(text: str) -> CandidateLock:
    """Read the pylock that ``uv pip compile --format pylock.toml`` printed."""
    data = tomllib.loads(text)
    candidates = tuple(
        Candidate(
            name=canonicalize_name(package["name"]),
            version=Version(package["version"]),
            wheels=tuple(
                CandidateWheel(
                    filename=unquote(urlsplit(wheel["url"]).path.rpartition("/")[2]),
                    url=wheel["url"],
                    size=wheel.get("size"),
                    hashes=supported_hashes(wheel.get("hashes", {}), "uv hashes"),
                )
                for wheel in package.get("wheels", ())
            ),
        )
        for package in data.get("packages", ())
    )
    return CandidateLock(candidates, data.get("requires-python"))


def yanked_releases(stderr: str) -> frozenset[tuple[str, Version]]:
    """Releases uv warned about as yanked (it accepts them when pinned exactly)."""
    return frozenset(
        (canonicalize_name(name), Version(version))
        for name, version in YANKED_RE.findall(stderr)
    )
