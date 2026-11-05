"""Choosing the root wheel and reading its metadata (F7, steps 1 and 2).

1. ``uv pip compile --no-deps --only-binary :all: --format pylock.toml``
   picks the root version and lists every compatible wheel with its hashes.
2. ``uv pip install --no-deps --require-hashes --target <tmp>`` installs
   exactly that version, restricted to those hashes, so uv picks the wheel it
   prefers for the target interpreter. The ``Tag`` lines of the installed
   ``WHEEL`` file tell which one it was; its METADATA holds the Pylock URL.

The root version is fixed from here on (F7).
"""

import tempfile
from dataclasses import dataclass
from email.parser import BytesHeaderParser
from email.policy import compat32
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import SpecifierSet
from packaging.tags import parse_tag
from packaging.utils import canonicalize_name, parse_wheel_filename

from uvlx.candidates import Candidate, CandidateWheel, parse_candidates, yanked_releases
from uvlx.errors import LockRejected, MetadataError, UvError, VerifyError
from uvlx.uv import Uv
from uvlx.wheel import RootWheel, root_from_metadata


@dataclass(frozen=True, slots=True)
class RootRequest:
    """What the user asked for, like ``app[extra]>=1.0``."""

    name: str
    extras: frozenset[str]
    specifier: SpecifierSet
    # As typed: the default command name, like uvx (``my_tool``, not ``my-tool``).
    raw_name: str

    def requirement(self) -> str:
        return f"{self.name}{self.specifier}"


def parse_root_request(text: str) -> RootRequest:
    try:
        requirement = Requirement(text)
    except InvalidRequirement as error:
        raise VerifyError(f"invalid requirement {text!r}: {error}") from error
    if requirement.url is not None or requirement.marker is not None:
        raise VerifyError(
            f"{text!r}: only a name, extras and a version specifier are allowed"
        )
    return RootRequest(
        name=canonicalize_name(requirement.name),
        extras=frozenset(canonicalize_name(extra) for extra in requirement.extras),
        specifier=requirement.specifier,
        raw_name=requirement.name,
    )


def compile_root(uv: Uv, request: RootRequest) -> Candidate:
    output = uv.compile(
        request.requirement(),
        "",
        ("--no-deps", "--only-binary", ":all:", "--format", "pylock.toml"),
    )
    packages = parse_candidates(output.stdout).packages
    if len(packages) != 1 or packages[0].name != request.name:
        raise UvError(f"uv did not resolve exactly {request.name}")
    candidate = packages[0]
    if (candidate.name, candidate.version) in yanked_releases(output.stderr):
        raise LockRejected(f"[C5.4] {candidate.name} {candidate.version} is yanked")
    if not candidate.wheels:
        raise UvError(f"uv found no wheel of {candidate.name} {candidate.version}")
    return candidate


def hashed_requirement(candidate: Candidate) -> str:
    """``name==version --hash=...`` allowing any of the candidate wheels."""
    hashes = sorted(
        f"--hash={algorithm}:{digest}"
        for wheel in candidate.wheels
        for algorithm, digest in wheel.hashes.items()
    )
    if not hashes:
        raise UvError(f"the index states no hash for {candidate.name}")
    return f"{candidate.name}=={candidate.version} {' '.join(hashes)}\n"


def read_dist_info(target: Path) -> tuple[bytes, bytes]:
    """METADATA and WHEEL of the single distribution in ``target``."""
    found = tuple(target.glob("*.dist-info"))
    if len(found) != 1:
        raise MetadataError(f"expected one dist-info after installing, got {found}")
    try:
        return (found[0] / "METADATA").read_bytes(), (found[0] / "WHEEL").read_bytes()
    except OSError as error:
        raise MetadataError(f"cannot read the installed dist-info: {error}") from error


def installed_wheel(candidate: Candidate, wheel_file: bytes) -> CandidateWheel:
    """The candidate wheel whose tags and build tag match the WHEEL file."""
    message = BytesHeaderParser(policy=compat32).parsebytes(wheel_file)
    tags = frozenset(
        str(tag)
        for line in message.get_all("Tag") or ()
        for tag in parse_tag(str(line).strip())
    )
    build = str(message.get("Build", "")).strip()
    matches: list[CandidateWheel] = []
    for wheel in candidate.wheels:
        _name, _version, wheel_build, wheel_tags = parse_wheel_filename(wheel.filename)
        build_text = ""
        if wheel_build:
            build_text = f"{wheel_build[0]}{wheel_build[1]}"
        if frozenset(str(tag) for tag in wheel_tags) == tags and build_text == build:
            matches.append(wheel)
    if len(matches) != 1:
        raise MetadataError(
            f"cannot tell which wheel of {candidate.name} uv installed "
            f"(tags {sorted(tags)}, {len(matches)} match)"
        )
    return matches[0]


def select_root(uv: Uv, request: RootRequest) -> RootWheel:
    """Pick the root wheel (F7) and read its metadata."""
    candidate = compile_root(uv, request)
    with tempfile.TemporaryDirectory(prefix="uvlx-root-") as directory:
        target = Path(directory)
        uv.install_target(hashed_requirement(candidate), target)
        metadata, wheel_file = read_dist_info(target)
    wheel = installed_wheel(candidate, wheel_file)
    root = root_from_metadata(metadata, wheel.filename, wheel.hashes)
    if root.version != candidate.version:
        raise MetadataError(f"{wheel.filename}: metadata version differs")
    return root
