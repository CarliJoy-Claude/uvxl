"""Reading the candidate pylock that ``uv pip compile`` prints."""

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final
from urllib.parse import unquote, urlsplit

from packaging.utils import canonicalize_name
from packaging.version import Version

from uvlx.errors import LockRejected, UvError
from uvlx.toml_values import (
    expect_mapping,
    expect_sequence,
    expect_str,
    supported_hashes,
)

NON_WHEEL_SOURCES: Final = ("vcs", "directory", "archive", "sdist")
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


def _parse_wheel(raw: object, where: str) -> CandidateWheel:
    wheel = expect_mapping(raw, where)
    url = expect_str(wheel.get("url"), f"{where}.url")
    size = wheel.get("size")
    if not isinstance(size, int):
        size = None
    return CandidateWheel(
        filename=unquote(urlsplit(url).path.rpartition("/")[2]),
        url=url,
        size=size,
        hashes=supported_hashes(wheel.get("hashes", {}), f"{where}.hashes"),
    )


def parse_candidates(text: str) -> CandidateLock:
    """Read the pylock that ``uv pip compile --format pylock.toml`` printed."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise UvError(f"uv printed an invalid pylock: {error}") from error
    candidates: list[Candidate] = []
    for position, raw in enumerate(expect_sequence(data.get("packages", ()), "uv")):
        where = f"uv packages[{position}]"
        table = expect_mapping(raw, where)
        name = canonicalize_name(expect_str(table.get("name"), f"{where}.name"))
        for source in NON_WHEEL_SOURCES:
            if source in table:
                raise LockRejected(f"[C5.4] uv proposes a {source} for {name}")
        wheels = tuple(
            _parse_wheel(wheel, f"{where}.wheels[{number}]")
            for number, wheel in enumerate(
                expect_sequence(table.get("wheels", ()), f"{where}.wheels")
            )
        )
        version = Version(expect_str(table.get("version"), f"{where}.version"))
        candidates.append(Candidate(name=name, version=version, wheels=wheels))
    requires_python = data.get("requires-python")
    if not isinstance(requires_python, str):
        requires_python = None
    return CandidateLock(tuple(candidates), requires_python)


def yanked_releases(stderr: str) -> frozenset[tuple[str, Version]]:
    """Releases uv warned about as yanked (it accepts them when pinned exactly)."""
    return frozenset(
        (canonicalize_name(name), Version(version))
        for name, version in YANKED_RE.findall(stderr)
    )
