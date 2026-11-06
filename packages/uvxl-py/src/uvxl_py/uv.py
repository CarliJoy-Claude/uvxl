"""Running the uv CLI, and querying the target interpreter.

uv has no Python API, so the PoC drives the CLI.
"""

import json
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final

import packaging

from uvxl_py.errors import LockRejectedError, VerifyError
from uvxl_py.venv import executable

PACKAGING_PATH: Final = str(Path(packaging.__file__).parent.parent)

# Prints the PEP 508 marker environment of the interpreter it runs in, with
# our own copy of packaging (pure Python) appended to that interpreter's path.
MARKER_ENVIRONMENT_SCRIPT: Final = (
    "import json, sys; sys.path.append(sys.argv[1]); "
    "from packaging.markers import default_environment; "
    "print(json.dumps(default_environment()))"
)


def without_noise(stderr: str) -> str:
    lines = tuple(line for line in stderr.splitlines() if "UV_NATIVE_TLS" not in line)
    return "\n".join(lines).strip()


@dataclass(frozen=True, slots=True)
class UvOutput:
    stdout: str
    stderr: str


def run_uv(executable: str, arguments: Sequence[str], stdin: str = "") -> UvOutput:
    """Run uv; any failure raises with uv's stderr."""
    process = subprocess.run(
        (executable, *arguments),
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )
    stderr = without_noise(process.stderr)
    if process.returncode != 0:
        raise VerifyError(f"uv {' '.join(arguments[:2])} failed:\n{stderr}")
    return UvOutput(process.stdout, stderr)


@dataclass(frozen=True, slots=True)
class Uv:
    """The uv executable, the target interpreter, and uvx options for uv.

    ``global_args`` go to every uv call (cache, network, configuration),
    ``resolver_args`` only to the ``uv pip`` calls (indexes, resolution).
    """

    executable: str
    python: str
    global_args: tuple[str, ...] = ()
    resolver_args: tuple[str, ...] = ()

    @property
    def pip_args(self) -> tuple[str, ...]:
        return (*self.global_args, *self.resolver_args)

    def with_python(self, python: str) -> "Uv":
        return Uv(self.executable, python, self.global_args, self.resolver_args)

    def cache_dir(self) -> Path:
        """uv's cache directory (``--cache-dir``, ``UV_CACHE_DIR``, default)."""
        output = run_uv(self.executable, ("cache", "dir", *self.global_args))
        return Path(output.stdout.strip())

    def compile(
        self, requirement: str, constraints: str, options: Sequence[str]
    ) -> UvOutput:
        """Run ``uv pip compile -``: the requirement on stdin, constraints in a file.

        "No solution found" means the lock's pins do not fit (C5).
        """
        with tempfile.TemporaryDirectory(prefix="uvxl-") as directory:
            constraints_file = Path(directory) / "constraints.txt"
            constraints_file.write_text(constraints, encoding="utf-8")
            arguments = (
                "pip",
                "compile",
                "-",
                "--constraints",
                str(constraints_file),
                "--python",
                self.python,
                "--no-header",
                *options,
                *self.pip_args,
            )
            try:
                return run_uv(self.executable, arguments, requirement + "\n")
            except VerifyError as error:
                if "No solution found" in str(error):
                    raise LockRejectedError(
                        f"[C5] uv finds no resolution within the lock's pins: {error}"
                    ) from error
                raise

    def install(self, lock: Path) -> None:
        """Rule C8: install a cleaned lock; uv enforces its hashes."""
        arguments = ("pip", "install", "--python", self.python, "-r", str(lock))
        run_uv(self.executable, (*arguments, *self.pip_args))

    def install_target(self, requirement: str, target: Path) -> None:
        """Install one wheel, without dependencies, into a plain directory."""
        arguments = (
            "pip",
            "install",
            "--python",
            self.python,
            "--target",
            str(target),
            "--no-deps",
            "--only-binary",
            ":all:",
            requirement,
        )
        run_uv(self.executable, (*arguments, *self.pip_args))

    def create_venv(self, path: Path) -> str:
        """Create (or recreate) a virtual environment for the target interpreter."""
        arguments = ("venv", "--quiet", "--clear", "--python", self.python, str(path))
        run_uv(self.executable, (*arguments, *self.global_args))
        return str(executable(path, "python"))


def find_uv() -> str:
    found = shutil.which("uv")
    if found is None:
        raise VerifyError("uv not found on PATH")
    return found


def find_interpreter(
    uv: str, request: str | None, global_args: Sequence[str] = ()
) -> str:
    """The interpreter uv would use (respecting .venv and uv's settings)."""
    arguments: tuple[str, ...] = ("python", "find")
    if request is not None:
        arguments = (*arguments, request)
    return run_uv(uv, (*arguments, *global_args)).stdout.strip()


def marker_environment(interpreter: str) -> Mapping[str, str]:
    """PEP 508 marker values of the target interpreter, not the running one."""
    result = subprocess.run(
        (interpreter, "-I", "-c", MARKER_ENVIRONMENT_SCRIPT, PACKAGING_PATH),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise VerifyError(f"cannot query {interpreter}: {result.stderr.strip()}")
    raw = json.loads(result.stdout)
    return MappingProxyType({str(key): str(value) for key, value in raw.items()})
