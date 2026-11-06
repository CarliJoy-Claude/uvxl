"""Typed access to parsed TOML values, and hash tables of pylock files."""

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Final

from uvxl_py.errors import LockRejected

SUPPORTED_HASHES: Final = ("sha256", "sha384", "sha512")


def expect_mapping(value: object, where: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise LockRejected(f"{where}: expected a table")
    return value


def expect_sequence(value: object, where: str) -> Sequence[object]:
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        raise LockRejected(f"{where}: expected an array")
    return value


def expect_str(value: object, where: str) -> str:
    if not isinstance(value, str):
        raise LockRejected(f"{where}: expected a string")
    return value


def supported_hashes(value: object, where: str) -> Mapping[str, str]:
    """Return the usable (sha256/384/512) hashes of a lock table."""
    table = expect_mapping(value, where)
    result = {
        algorithm.lower(): expect_str(digest, f"{where}.{algorithm}").lower()
        for algorithm, digest in table.items()
        if algorithm.lower() in SUPPORTED_HASHES
    }
    return MappingProxyType(result)


def common_verified(
    lock_hashes: Mapping[str, str], file_hashes: Mapping[str, str]
) -> Mapping[str, str] | None:
    """Hashes both sides state. None if there are none or if any disagree."""
    common = sorted(lock_hashes.keys() & file_hashes.keys())
    if not common:
        return None
    for algorithm in common:
        if lock_hashes[algorithm] != file_hashes[algorithm]:
            return None
    return MappingProxyType({algorithm: lock_hashes[algorithm] for algorithm in common})
