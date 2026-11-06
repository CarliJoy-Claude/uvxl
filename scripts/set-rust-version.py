"""Set the version of uvxl-rs (Cargo.toml and Cargo.lock), e.g. from a tag.

Usage: python scripts/set-rust-version.py v1.2.3   (a leading "v" is dropped)

The Python packages get their version from the git tag through hatch-vcs;
maturin takes uvxl-rs's version from Cargo.toml, so releases set it here.
"""

import re
import sys
from pathlib import Path

CRATE = Path(__file__).parents[1] / "packages" / "uvxl-rs"


def set_version(version: str) -> None:
    manifest = CRATE / "Cargo.toml"
    text, count = re.subn(
        r'^version = "[^"]*"$',
        f'version = "{version}"',
        manifest.read_text(encoding="utf-8"),
        count=1,
        flags=re.MULTILINE,
    )
    if count != 1:
        raise SystemExit(f"no version line in {manifest}")
    manifest.write_text(text, encoding="utf-8")

    lock = CRATE / "Cargo.lock"
    text, count = re.subn(
        r'(name = "uvxl-rs"\nversion = )"[^"]*"',
        rf'\g<1>"{version}"',
        lock.read_text(encoding="utf-8"),
    )
    if count != 1:
        raise SystemExit(f"no uvxl-rs entry in {lock}")
    lock.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    set_version(sys.argv[1].removeprefix("v"))
