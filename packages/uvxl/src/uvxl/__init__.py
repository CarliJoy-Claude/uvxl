"""``uvxl``: run a package with the lock its ``Pylock`` Project-URL links.

This meta package picks a backend: ``uvxl-rs`` (Rust, on uv's crates) if it
is installed, otherwise ``uvxl-py`` (Python, drives the uv command line).
``UVXL_BACKEND=rust`` or ``UVXL_BACKEND=python`` forces one; it is an error
if that one is not installed. Install the Rust backend with ``uvxl[rust]``.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import sysconfig
from collections.abc import Sequence
from typing import Final, NoReturn

BACKENDS: Final = ("rust", "python")
RUST_COMMAND: Final = "uvxl-rs"


class BackendError(Exception):
    """The requested backend is unknown or not installed."""


def select_backend(requested: str, rust: str | None, python: bool) -> str:
    """The backend to use: the requested one, else Rust before Python."""
    if requested and requested not in BACKENDS:
        raise BackendError(
            f"UVXL_BACKEND={requested!r} is not one of {', '.join(BACKENDS)}"
        )
    if requested == "rust" and rust is None:
        raise BackendError(
            "UVXL_BACKEND=rust, but uvxl-rs is not installed (install uvxl[rust])"
        )
    if requested == "python" and not python:
        raise BackendError("UVXL_BACKEND=python, but uvxl-py is not installed")
    if requested:
        return requested
    if rust is not None:
        return "rust"
    if python:
        return "python"
    raise BackendError("no uvxl backend is installed (install uvxl-py or uvxl-rs)")


def find_rust() -> str | None:
    """``uvxl-rs`` in this environment's scripts directory.

    Not PATH: ``uv tool install 'uvxl[rust]'`` links only uvxl's own
    commands onto PATH, the backend stays in the tool environment.
    """
    return shutil.which(RUST_COMMAND, path=sysconfig.get_path("scripts"))


def python_available() -> bool:
    return importlib.util.find_spec("uvxl_py") is not None


def main(argv: Sequence[str] | None = None) -> NoReturn:
    """The ``uvxl`` command: run the selected backend with the same arguments."""
    if argv is None:
        argv = sys.argv[1:]
    rust = find_rust()
    try:
        backend = select_backend(
            os.environ.get("UVXL_BACKEND", ""), rust, python_available()
        )
    except BackendError as error:
        sys.stderr.write(f"uvxl: error: {error}\n")
        raise SystemExit(1) from error
    if backend == "rust" and rust is not None:
        raise SystemExit(subprocess.run((rust, *argv), check=False).returncode)
    from uvxl_py.cli import uvxl_main  # noqa: PLC0415 (only for this backend)

    uvxl_main.main(args=list(argv), prog_name="uvxl")
    raise SystemExit(0)  # pragma: no cover (click exits itself)
