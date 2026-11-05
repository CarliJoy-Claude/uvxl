"""Verify and clean (spec section 3.4), with the uv CLI as the engine.

1. The lock is checked on its own (N1, C1 to C4, see ``uvlx.lock``).
2. ``uv pip compile`` resolves the root requirement against the configured
   indexes, with the lock's pins as constraints. Constraints never add
   packages, so lock entries nobody needs are never looked up (C5, C6).
   The output is a candidate pylock listing what the indexes really serve.
3. The candidate is checked against the lock: every package must be locked
   with the same version (C5.1, C5.2), the lock's index must be a configured
   one (C5.3), and only wheels whose hash the lock lists are kept (C5.4, C5.5).
   The root must be the given wheel (C7).
4. The cleaned lock is rendered; ``Uv.install`` installs it with hash
   enforcement (C8).

Known gap: uv accepts a yanked version when it is pinned exactly and only
warns on stderr. That warning is parsed, which is fragile.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import tomli_w
from packaging.utils import canonicalize_name
from packaging.version import Version

from uvlx.candidates import Candidate, parse_candidates, yanked_releases
from uvlx.errors import LockRejected, UvError, VerifyError
from uvlx.lock import LockEntry, lock_environment, parse_lock
from uvlx.toml_values import common_verified
from uvlx.urls import IndexKey, configured_index_for, index_key, strip_user_info
from uvlx.uv import Uv, marker_environment
from uvlx.wheel import RootWheel, load_root_wheel

INDEX_LINE_RE: Final = re.compile(r"^--(?:index-url|extra-index-url)\s+(\S+)\s*$")
CREATED_BY: Final = "pylock-verify-clean"


@dataclass(frozen=True, slots=True)
class CleanWheel:
    filename: str
    url: str
    size: int | None
    hashes: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class CleanPackage:
    name: str
    version: Version
    index: str | None
    wheels: tuple[CleanWheel, ...]


@dataclass(frozen=True, slots=True)
class CleanResult:
    packages: tuple[CleanPackage, ...]
    unreached: tuple[str, ...]
    requires_python: str | None


def check_root(
    candidate: Candidate, root: RootWheel, configured: Sequence[str]
) -> CleanPackage:
    """Rule C7: the root is exactly the given wheel."""
    if candidate.version != root.version:
        raise LockRejected(
            f"[C7] uv selected {root.name} {candidate.version}, expected {root.version}"
        )
    for wheel in candidate.wheels:
        verified = common_verified(root.hashes, wheel.hashes)
        if verified is not None and wheel.filename == root.filename:
            clean_wheel = CleanWheel(wheel.filename, wheel.url, wheel.size, verified)
            index = configured_index_for(wheel.url, configured)
            return CleanPackage(root.name, root.version, index, (clean_wheel,))
    raise LockRejected(
        f"[C7] the configured indexes do not serve {root.filename} "
        f"with hashes {dict(root.hashes)}"
    )


def check_locked(
    candidate: Candidate,
    entry: LockEntry | None,
    configured_keys: frozenset[IndexKey],
) -> CleanPackage:
    """Rules C5.1 to C5.5 for one package uv needs."""
    if entry is None:
        raise LockRejected(
            f"[C5.1] {candidate.name} {candidate.version} is needed but not in the lock"
        )
    if candidate.version != entry.version:
        raise LockRejected(
            f"[C5.2] uv selected {candidate.name} {candidate.version}, "
            f"the lock has {entry.version}"
        )
    if index_key(entry.index) not in configured_keys:
        raise LockRejected(
            f"[C5.3] the lock names index {entry.index!r} for {entry.name}, "
            "which is not a configured index"
        )
    kept: list[CleanWheel] = []
    for wheel in candidate.wheels:
        for lock_hashes in entry.wheel_hashes:
            verified = common_verified(lock_hashes, wheel.hashes)
            if verified is not None:
                kept.append(CleanWheel(wheel.filename, wheel.url, wheel.size, verified))
                break
    if not kept:
        raise LockRejected(
            f"[C5.4] no file of {entry.name} {entry.version} served by the "
            "configured indexes matches the lock"
        )
    return CleanPackage(entry.name, entry.version, entry.index, tuple(kept))


def configured_indexes(uv: Uv, requirement: str) -> tuple[str, ...]:
    """The indexes uv is configured with (CLI, env vars and config files).

    The URLs keep their user info: it is one source of credentials (F3).
    """
    output = uv.compile(
        requirement,
        "",
        (
            "--no-deps",
            "--emit-index-url",
            "--no-annotate",
            "--format",
            "requirements.txt",
        ),
    )
    found = (INDEX_LINE_RE.match(line) for line in output.stdout.splitlines())
    return tuple(match.group(1) for match in found if match)


def root_requirement(root: RootWheel, extras: frozenset[str]) -> str:
    if extras:
        return f"{root.name}[{','.join(sorted(extras))}]=={root.version}"
    return f"{root.name}=={root.version}"


def clean(
    wheel: Path,
    lock_path: Path,
    uv: Uv,
    extras: Sequence[str] = (),
) -> CleanResult:
    """Verify ``lock_path`` for a local root ``wheel``; see ``clean_root``."""
    return clean_root(
        load_root_wheel(wheel), lock_path.name, lock_path.read_bytes(), uv, extras
    )


def clean_root(
    root: RootWheel,
    lock_name: str,
    lock_content: bytes,
    uv: Uv,
    extras: Sequence[str] = (),
) -> CleanResult:
    """Verify a lock for the chosen root wheel and build the cleaned lock."""
    requested = frozenset(canonicalize_name(extra) for extra in extras)
    unknown = requested - root.provides_extra
    if unknown:
        raise VerifyError(
            f"{root.filename} has no extra(s): {', '.join(sorted(unknown))}"
        )
    environment = lock_environment(marker_environment(uv.python), requested)
    lock = parse_lock(lock_name, lock_content, root.name, environment)

    constraints = "".join(
        f"{entry.name}=={entry.version}\n"
        for entry in sorted(lock.values(), key=lambda item: item.name)
    )
    output = uv.compile(
        root_requirement(root, requested),
        constraints,
        ("--format", "pylock.toml", "--only-binary", ":all:"),
    )
    candidates = parse_candidates(output.stdout)
    yanked = yanked_releases(output.stderr)
    configured = tuple(
        strip_user_info(index)
        for index in configured_indexes(uv, root_requirement(root, frozenset()))
    )
    configured_keys = frozenset(index_key(index) for index in configured)

    packages: list[CleanPackage] = []
    for candidate in candidates.packages:
        if (candidate.name, candidate.version) in yanked:
            raise LockRejected(f"[C5.4] {candidate.name} {candidate.version} is yanked")
        if candidate.name == root.name:
            packages.append(check_root(candidate, root, configured))
        else:
            entry = lock.get(candidate.name)
            packages.append(check_locked(candidate, entry, configured_keys))

    names = frozenset(package.name for package in packages)
    if root.name not in names:
        raise UvError(f"uv's resolution does not contain {root.name}")
    return CleanResult(
        packages=tuple(sorted(packages, key=lambda package: package.name)),
        unreached=tuple(sorted(set(lock) - names)),
        requires_python=candidates.requires_python,
    )


def render(result: CleanResult) -> str:
    """The cleaned lock as pylock.toml text."""
    entries: list[dict[str, object]] = []
    for package in result.packages:
        wheels: list[dict[str, object]] = []
        for wheel in package.wheels:
            table: dict[str, object] = {"name": wheel.filename, "url": wheel.url}
            if wheel.size is not None:
                table["size"] = wheel.size
            table["hashes"] = dict(sorted(wheel.hashes.items()))
            wheels.append(table)
        entry: dict[str, object] = {
            "name": package.name,
            "version": str(package.version),
        }
        if package.index is not None:
            entry["index"] = package.index
        entry["wheels"] = wheels
        entries.append(entry)
    document: dict[str, object] = {"lock-version": "1.0", "created-by": CREATED_BY}
    if result.requires_python is not None:
        document["requires-python"] = result.requires_python
    document["packages"] = entries
    return tomli_w.dumps(document)
