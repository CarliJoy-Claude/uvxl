"""Verify and clean (spec section 3.4), with the uv CLI as the engine.

1. The lock is checked on its own (N1, C1 to C4, see ``uvlx.lock``).
2. ``uv pip compile`` resolves the root requirement against the configured
   indexes, with the lock's pins as constraints. Constraints never add
   packages, so lock entries nobody needs are never looked up (C5, C6).
   The output is a candidate pylock listing what the indexes really serve.
3. The candidate is checked against the lock: every package must be locked
   with the same version (C5.1, C5.2), the lock's index must be a configured
   one (C5.3), and only wheels whose hash the lock lists are kept (C5.4, C5.5).
   The root keeps every wheel the index serves for its version (C7).
4. The cleaned lock is rendered; ``Uv.install`` installs it with hash
   enforcement (C8).

Known gap: uv accepts a yanked version when it is pinned exactly and only
warns on stderr. That warning is parsed, which is fragile.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Final

import tomli_w
from packaging.utils import canonicalize_name
from packaging.version import Version

from uvlx.candidates import Candidate, Wheel, parse_candidates, yanked_releases
from uvlx.errors import LockRejected, VerifyError
from uvlx.lock import LockEntry, lock_environment, parse_lock
from uvlx.root import RootWheel
from uvlx.toml_values import common_verified
from uvlx.urls import IndexKey, index_key, strip_user_info
from uvlx.uv import Uv, marker_environment

INDEX_LINE_RE: Final = re.compile(r"^--(?:index-url|extra-index-url)\s+(\S+)\s*$")
CREATED_BY: Final = "pylock-verify-clean"


@dataclass(frozen=True, slots=True)
class CleanPackage:
    name: str
    version: Version
    index: str | None
    wheels: tuple[Wheel, ...]


@dataclass(frozen=True, slots=True)
class CleanResult:
    packages: tuple[CleanPackage, ...]
    unreached: tuple[str, ...]


def check_root(candidate: Candidate, root: RootWheel) -> CleanPackage:
    """Rule C7: every wheel the index serves for the root version (uv chose
    the wheel), with URL and hash from the index.

    The entry has no ``index``: PEP 751 only says SHOULD, and uv then uses
    the file URL minus the file name, which only affects its cache key.
    """
    if candidate.version != root.version:
        raise LockRejected(
            f"[C7] uv selected {root.name} {candidate.version}, expected {root.version}"
        )
    kept: list[Wheel] = []
    for wheel in candidate.wheels:
        if wheel.hashes:
            kept.append(wheel)
    if not kept:
        raise LockRejected(
            f"[C7] the configured indexes serve no hashed wheel for the root "
            f"{root.name} {root.version}"
        )
    return CleanPackage(root.name, root.version, None, tuple(kept))


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
    kept: list[Wheel] = []
    for wheel in candidate.wheels:
        for lock_hashes in entry.wheel_hashes:
            verified = common_verified(lock_hashes, wheel.hashes)
            if verified is not None:
                kept.append(replace(wheel, hashes=verified))
                break
    if not kept:
        raise LockRejected(
            f"[C5.4] no file of {entry.name} {entry.version} served by the "
            "configured indexes matches the lock"
        )
    return CleanPackage(entry.name, entry.version, entry.index, tuple(kept))


def configured_indexes(uv: Uv, requirement: str) -> tuple[str, ...]:
    """The indexes uv is configured with (CLI, env vars and config files)."""
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
    return tuple(strip_user_info(match.group(1)) for match in found if match)


def root_requirement(root: RootWheel, extras: frozenset[str]) -> str:
    if extras:
        return f"{root.name}[{','.join(sorted(extras))}]=={root.version}"
    return f"{root.name}=={root.version}"


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
            f"{root.name} {root.version} has no extra(s): {', '.join(sorted(unknown))}"
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
    configured = configured_indexes(uv, root_requirement(root, frozenset()))
    configured_keys = frozenset(index_key(index) for index in configured)

    packages: list[CleanPackage] = []
    for candidate in candidates:
        if (candidate.name, candidate.version) in yanked:
            raise LockRejected(f"[C5.4] {candidate.name} {candidate.version} is yanked")
        if candidate.name == root.name:
            packages.append(check_root(candidate, root))
        else:
            entry = lock.get(candidate.name)
            packages.append(check_locked(candidate, entry, configured_keys))

    names = frozenset(package.name for package in packages)
    if root.name not in names:
        raise VerifyError(f"uv's resolution does not contain {root.name}")
    return CleanResult(
        packages=tuple(sorted(packages, key=lambda package: package.name)),
        unreached=tuple(sorted(set(lock) - names)),
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
    document["packages"] = entries
    return tomli_w.dumps(document)
