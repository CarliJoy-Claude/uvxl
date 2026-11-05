"""The root wheel chosen by the installer."""

import hashlib
import io
import re
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from packaging.metadata import Metadata
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

from uvlx.errors import MetadataError, VerifyError


@dataclass(frozen=True, slots=True)
class RootWheel:
    name: str
    version: Version
    filename: str
    hashes: Mapping[str, str]
    provides_extra: frozenset[str]
    metadata: bytes


def root_from_metadata(
    raw: bytes, filename: str, hashes: Mapping[str, str]
) -> RootWheel:
    """The root wheel from its METADATA bytes, file name and file hashes."""
    try:
        file_name, file_version, _build, _tags = parse_wheel_filename(filename)
    except ValueError as error:
        raise VerifyError(f"{filename}: not a valid wheel file name") from error
    try:
        metadata = Metadata.from_email(raw, validate=False)
        name = metadata.name
        version = metadata.version
        extras = frozenset(
            canonicalize_name(extra) for extra in metadata.provides_extra or ()
        )
    except (ValueError, ExceptionGroup) as error:
        raise MetadataError(f"{filename}: invalid metadata: {error}") from error
    if name is None or version is None:
        raise MetadataError(f"{filename}: metadata lacks Name or Version")
    if canonicalize_name(name) != file_name or version != file_version:
        raise MetadataError(f"{filename}: file name and metadata disagree")
    return RootWheel(
        name=file_name,
        version=version,
        filename=filename,
        hashes=MappingProxyType(dict(hashes)),
        provides_extra=extras,
        metadata=raw,
    )


def load_root_wheel(path: Path) -> RootWheel:
    """The root wheel from a local file."""
    data = path.read_bytes()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            candidates = tuple(
                name
                for name in archive.namelist()
                if re.fullmatch(r"[^/]+\.dist-info/METADATA", name)
            )
            if len(candidates) != 1:
                raise MetadataError(f"{path.name}: expected one dist-info/METADATA")
            raw = archive.read(candidates[0])
    except zipfile.BadZipFile as error:
        raise MetadataError(f"{path.name}: not a valid wheel archive") from error
    return root_from_metadata(
        raw, path.name, {"sha256": hashlib.sha256(data).hexdigest()}
    )
