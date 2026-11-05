"""The root wheel chosen by the installer."""

import hashlib
import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from packaging.metadata import Metadata
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

from uvlx.errors import MetadataError, VerifyError


@dataclass(frozen=True, slots=True)
class RootWheel:
    name: str
    version: Version
    filename: str
    sha256: str
    provides_extra: frozenset[str]


def load_root_wheel(path: Path) -> RootWheel:
    data = path.read_bytes()
    try:
        file_name, file_version, _build, _tags = parse_wheel_filename(path.name)
    except ValueError as error:
        raise VerifyError(f"{path.name}: not a valid wheel file name") from error
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
    try:
        metadata = Metadata.from_email(raw, validate=False)
        name = metadata.name
        version = metadata.version
        extras = frozenset(
            canonicalize_name(extra) for extra in metadata.provides_extra or ()
        )
    except (ValueError, ExceptionGroup) as error:
        raise MetadataError(f"{path.name}: invalid metadata: {error}") from error
    if name is None or version is None:
        raise MetadataError(f"{path.name}: metadata lacks Name or Version")
    if canonicalize_name(name) != file_name or version != file_version:
        raise MetadataError(f"{path.name}: file name and metadata disagree")
    return RootWheel(
        name=file_name,
        version=version,
        filename=path.name,
        sha256=hashlib.sha256(data).hexdigest(),
        provides_extra=extras,
    )
