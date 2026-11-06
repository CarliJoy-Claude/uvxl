"""The ``uvxl`` meta package: which backend runs."""

import os
import subprocess
import sys
import sysconfig
from collections.abc import Sequence
from pathlib import Path

import pytest

import uvxl
from helpers import UV

ROOT = Path(__file__).parents[1]
# Help texts tell the backends apart: uv's own uvx text, or uvxl-py's.
RUST_HELP = "Run a command provided by a Python package"
PYTHON_HELP = "Run COMMAND from a package installed with the lock"


@pytest.mark.parametrize(
    ("requested", "rust", "python", "chosen"),
    [
        ("", "/bin/uvxl-rs", True, "rust"),
        ("", None, True, "python"),
        ("", "/bin/uvxl-rs", False, "rust"),
        ("rust", "/bin/uvxl-rs", True, "rust"),
        ("python", "/bin/uvxl-rs", True, "python"),
        ("python", None, True, "python"),
    ],
)
def test_select_backend(
    requested: str, rust: str | None, python: bool, chosen: str
) -> None:
    assert uvxl.select_backend(requested, rust, python) == chosen


@pytest.mark.parametrize(
    ("requested", "rust", "python", "message"),
    [
        ("go", "/bin/uvxl-rs", True, "is not one of rust, python"),
        ("rust", None, True, r"uvxl-rs is not installed \(install uvxl\[rust\]\)"),
        ("python", "/bin/uvxl-rs", False, "uvxl-py is not installed"),
        ("", None, False, "no uvxl backend is installed"),
    ],
)
def test_select_backend_errors(
    requested: str, rust: str | None, python: bool, message: str
) -> None:
    with pytest.raises(uvxl.BackendError, match=message):
        uvxl.select_backend(requested, rust, python)


def test_both_backends_are_installed_in_the_dev_environment() -> None:
    assert uvxl.find_rust() is not None
    assert uvxl.python_available()


def run_uvxl(
    *arguments: str, backend: str | None = None, python: str = sys.executable
) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment.pop("UVXL_BACKEND", None)
    if backend is not None:
        environment["UVXL_BACKEND"] = backend
    return subprocess.run(
        (python, "-m", "uvxl", *arguments),
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )


@pytest.mark.parametrize(
    ("backend", "expected"),
    [(None, RUST_HELP), ("rust", RUST_HELP), ("python", PYTHON_HELP)],
)
def test_uvxl_runs_the_selected_backend(backend: str | None, expected: str) -> None:
    result = run_uvxl("--help", backend=backend)
    assert result.returncode == 0, result.stderr
    assert expected in result.stdout


def test_unknown_backend_is_an_error() -> None:
    result = run_uvxl("--help", backend="go")
    assert result.returncode == 1
    assert "uvxl: error: UVXL_BACKEND='go'" in result.stderr


def test_pylock_verify_is_available_through_uvxl() -> None:
    scripts = Path(sysconfig.get_path("scripts"))
    assert any(scripts.glob("pylock-verify*"))


# --------------------------------------------------------------------------
# Real environments without a backend (no mocks): a fresh virtual environment
# sees only the given source directories, plus the dev site-packages for
# third-party dependencies like click (not their .pth files, so no backend).
# --------------------------------------------------------------------------


def bare_environment(tmp_path: Path, sources: Sequence[Path]) -> str:
    assert UV is not None
    venv = tmp_path / "venv"
    subprocess.run(
        (UV, "venv", "--quiet", "--python", sys.executable, str(venv)),
        check=True,
    )
    python = str(venv / Path(sysconfig.get_path("scripts")).name / "python")
    purelib = subprocess.run(
        (python, "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    paths = [*sources, Path(sysconfig.get_path("purelib"))]
    (Path(purelib) / "uvxl_test.pth").write_text(
        "".join(f"{path}\n" for path in paths), encoding="utf-8"
    )
    return python


@pytest.mark.skipif(UV is None, reason="uv is not installed")
def test_without_rust_the_python_backend_runs(tmp_path: Path) -> None:
    python = bare_environment(
        tmp_path, (ROOT / "packages/uvxl/src", ROOT / "packages/uvxl-py/src")
    )
    result = run_uvxl("--help", python=python)
    assert result.returncode == 0, result.stderr
    assert PYTHON_HELP in result.stdout
    forced = run_uvxl("--help", backend="rust", python=python)
    assert forced.returncode == 1
    assert "uvxl-rs is not installed" in forced.stderr


@pytest.mark.skipif(UV is None, reason="uv is not installed")
def test_without_any_backend_uvxl_fails(tmp_path: Path) -> None:
    python = bare_environment(tmp_path, (ROOT / "packages/uvxl/src",))
    result = run_uvxl("--help", python=python)
    assert result.returncode == 1
    assert "no uvxl backend is installed" in result.stderr
    forced = run_uvxl("--help", backend="python", python=python)
    assert forced.returncode == 1
    assert "uvxl-py is not installed" in forced.stderr
