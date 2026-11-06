"""Choosing the root wheel and reading its metadata (F7).

uv chooses: ``uv pip install --no-deps --target <tmp>`` installs the wheel uv
prefers for the target interpreter, and its METADATA holds the Pylock URL.
From here on the root is bound by name and version (F7, C7).
"""

import tempfile
from dataclasses import dataclass
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name

from uvlx.errors import MetadataError, VerifyError
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


def read_metadata(target: Path) -> bytes:
    """METADATA of the single distribution installed in ``target``."""
    found = tuple(target.glob("*.dist-info/METADATA"))
    if len(found) != 1:
        raise MetadataError(f"expected one dist-info after installing, got {found}")
    return found[0].read_bytes()


def select_root(uv: Uv, request: RootRequest) -> RootWheel:
    """Let uv pick and install the root (no dependencies); read its METADATA."""
    with tempfile.TemporaryDirectory(prefix="uvlx-root-") as directory:
        target = Path(directory)
        uv.install_target(request.requirement(), target)
        metadata = read_metadata(target)
    root = root_from_metadata(metadata, request.requirement())
    if root.name != request.name:
        raise MetadataError(f"uv installed {root.name}, expected {request.name}")
    return root
