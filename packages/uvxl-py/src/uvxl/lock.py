"""Reading the maintainer's lock and checking it on its own (N1, C1 to C4)."""

import re
import tomllib
from collections.abc import Mapping, Set
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from packaging.markers import Marker
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

from uvxl.errors import LockRejected
from uvxl.toml_values import (
    expect_mapping,
    expect_sequence,
    expect_str,
    supported_hashes,
)

PYLOCK_NAME_RE: Final = re.compile(r"^pylock\.([^.]+\.)?toml$")

type Environment = Mapping[str, str | Set[str]]


@dataclass(frozen=True, slots=True)
class LockEntry:
    name: str
    version: Version
    index: str
    wheel_hashes: tuple[Mapping[str, str], ...]


def is_pylock_name(name: str) -> bool:
    """Rule N1: the PEP 751 file name pattern."""
    return PYLOCK_NAME_RE.fullmatch(name) is not None


def lock_environment(markers: Mapping[str, str], extras: frozenset[str]) -> Environment:
    environment: dict[str, str | Set[str]] = dict(markers)
    environment["extras"] = extras
    environment["dependency_groups"] = frozenset[str]()
    return MappingProxyType(environment)


def evaluate_lock_marker(raw: object, environment: Environment, where: str) -> bool:
    try:
        marker = Marker(expect_str(raw, where))
        return marker.evaluate(environment, context="lock_file")
    except ValueError as error:
        raise LockRejected(f"{where}: invalid marker: {error}") from error


def check_lock_header(data: Mapping[str, object], environment: Environment) -> None:
    """Rule C1: supported version, and the lock covers this environment."""
    if data.get("lock-version") != "1.0":
        raise LockRejected(
            f"[C1] unsupported lock-version {data.get('lock-version')!r}"
        )
    environments = data.get("environments")
    if environments is not None and not any(
        evaluate_lock_marker(marker, environment, "environments")
        for marker in expect_sequence(environments, "environments")
    ):
        raise LockRejected("[C1] the lock does not cover this environment")
    requires_python = data.get("requires-python")
    if requires_python is None:
        return
    python = str(environment["python_full_version"])
    try:
        covered = SpecifierSet(expect_str(requires_python, "requires-python")).contains(
            python, prereleases=True
        )
    except ValueError as error:
        raise LockRejected(f"[C1] invalid requires-python: {error}") from error
    if not covered:
        raise LockRejected(f"[C1] requires-python excludes Python {python}")


def check_entry_sources(table: Mapping[str, object], where: str) -> None:
    """Rule C3 for one entry: registry sources only, hashes everywhere."""
    for forbidden in ("vcs", "directory", "archive"):
        if forbidden in table:
            raise LockRejected(f"[C3] {where} uses a {forbidden!r} source")
    wheels = expect_sequence(table.get("wheels", ()), f"{where}.wheels")
    sdist = table.get("sdist")
    if not wheels and sdist is None:
        raise LockRejected(f"[C3] {where} lists no wheel and no sdist")
    sources = tuple(
        expect_mapping(wheel, f"{where}.wheels[{position}]")
        for position, wheel in enumerate(wheels)
    )
    if sdist is not None:
        sources = (*sources, expect_mapping(sdist, f"{where}.sdist"))
    for source in sources:
        if not supported_hashes(source.get("hashes", {}), f"{where} hashes"):
            raise LockRejected(
                f"[C3] {where} has a file without a usable sha256/sha384/sha512 hash"
            )


def parse_lock_entry(table: Mapping[str, object], name: str, where: str) -> LockEntry:
    try:
        version = Version(expect_str(table.get("version"), f"[C3] {where}.version"))
    except ValueError as error:
        raise LockRejected(f"[C3] {where}: invalid version: {error}") from error
    index = expect_str(table.get("index"), f"[C3] {where}.index")
    wheel_hashes = tuple(
        supported_hashes(
            expect_mapping(wheel, f"{where}.wheels[{position}]").get("hashes"),
            f"{where}.wheels[{position}].hashes",
        )
        for position, wheel in enumerate(
            expect_sequence(table.get("wheels", ()), f"{where}.wheels")
        )
    )
    return LockEntry(name, version, index, wheel_hashes)


def read_lock_toml(file_name: str, content: bytes) -> Mapping[str, object]:
    """Rule N1 on the file name, then the TOML document."""
    if not is_pylock_name(file_name):
        raise LockRejected(f"[N1] {file_name!r} is not a valid PEP 751 lock file name")
    try:
        return tomllib.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise LockRejected(f"{file_name}: invalid TOML: {error}") from error


def parse_lock(
    file_name: str, content: bytes, root_name: str, environment: Environment
) -> Mapping[str, LockEntry]:
    """Parse and check a lock; return the applicable entry per package name."""
    data = read_lock_toml(file_name, content)
    check_lock_header(data, environment)  # C1

    selected: dict[str, list[LockEntry]] = {}
    packages = expect_sequence(data.get("packages", ()), "packages")
    for position, raw in enumerate(packages):
        table = expect_mapping(raw, f"packages[{position}]")
        name = canonicalize_name(
            expect_str(table.get("name"), f"packages[{position}].name")
        )
        where = f"packages[{position}] ({name})"
        if name == root_name:  # C2: the root entry is ignored, whatever its source
            continue
        check_entry_sources(table, where)  # C3
        if "marker" in table and not evaluate_lock_marker(  # C4
            table["marker"], environment, f"{where}.marker"
        ):
            continue
        selected.setdefault(name, []).append(parse_lock_entry(table, name, where))

    for locked_name, entries in selected.items():
        if len(entries) > 1:
            raise LockRejected(
                f"[C4] {len(entries)} entries for {locked_name} "
                "apply to this environment"
            )
    return MappingProxyType(
        {locked_name: entries[0] for locked_name, entries in selected.items()}
    )


def verify_entries(file_name: str, content: bytes, package: str) -> int:
    """``pylock-verify``: are all entries allowed? Returns their number.

    Allowed are registry entries (wheels and sdists with hashes, version and
    index, C3) and one ``directory`` entry for ``package`` itself, as written
    by ``uv export`` (C2). Nothing else is checked.
    """
    data = read_lock_toml(file_name, content)
    root = canonicalize_name(package)
    packages = expect_sequence(data.get("packages", ()), "packages")
    directories = 0
    for position, raw in enumerate(packages):
        table = expect_mapping(raw, f"packages[{position}]")
        name = canonicalize_name(
            expect_str(table.get("name"), f"packages[{position}].name")
        )
        where = f"packages[{position}] ({name})"
        if name == root and "directory" in table:
            directories += 1
            if directories > 1:
                raise LockRejected(f"[C2] more than one directory entry for {root}")
            continue
        check_entry_sources(table, where)
        parse_lock_entry(table, name, where)
    return len(packages)
