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

from uvlx.errors import LockRejected, UvError

# Prints the PEP 508 marker environment of the interpreter it runs in.
# Mirrors packaging.markers.default_environment(), which the target
# interpreter may not have installed.
MARKER_ENVIRONMENT_SCRIPT: Final = """
import json, os, platform, sys

def full_version(info):
    version = f"{info.major}.{info.minor}.{info.micro}"
    if info.releaselevel != "final":
        version += info.releaselevel[0] + str(info.serial)
    return version

print(json.dumps({
    "implementation_name": sys.implementation.name,
    "implementation_version": full_version(sys.implementation.version),
    "os_name": os.name,
    "platform_machine": platform.machine(),
    "platform_release": platform.release(),
    "platform_system": platform.system(),
    "platform_version": platform.version(),
    "python_full_version": platform.python_version(),
    "platform_python_implementation": platform.python_implementation(),
    "python_version": ".".join(platform.python_version_tuple()[:2]),
    "sys_platform": sys.platform,
}))
"""


def without_noise(stderr: str) -> str:
    lines = tuple(line for line in stderr.splitlines() if "UV_NATIVE_TLS" not in line)
    return "\n".join(lines).strip()


@dataclass(frozen=True, slots=True)
class CompileOutput:
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class Uv:
    """The uv executable, the target interpreter, and extra arguments."""

    executable: str
    python: str
    extra_args: tuple[str, ...] = ()

    def compile(
        self, requirement: str, constraints: str, options: Sequence[str]
    ) -> CompileOutput:
        """Run ``uv pip compile -``: the requirement on stdin, constraints in a file."""
        with tempfile.TemporaryDirectory(prefix="uvlx-") as directory:
            constraints_file = Path(directory) / "constraints.txt"
            constraints_file.write_text(constraints, encoding="utf-8")
            command = (
                self.executable,
                "pip",
                "compile",
                "-",
                "--constraints",
                str(constraints_file),
                "--python",
                self.python,
                "--no-header",
                *options,
                *self.extra_args,
            )
            process = subprocess.run(
                command,
                input=requirement + "\n",
                capture_output=True,
                text=True,
                check=False,
            )
        stderr = without_noise(process.stderr)
        if process.returncode != 0:
            if "No solution found" in stderr:
                raise LockRejected(
                    "[C5] uv finds no resolution within the lock's pins:\n" + stderr
                )
            raise UvError(f"uv pip compile failed:\n{stderr}")
        return CompileOutput(process.stdout, stderr)

    def install(self, lock: Path) -> None:
        """Rule C8: install a cleaned lock; uv enforces its hashes."""
        command = (
            self.executable,
            "pip",
            "install",
            "--python",
            self.python,
            "-r",
            str(lock),
            *self.extra_args,
        )
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            message = without_noise(result.stderr)
            raise UvError(f"uv pip install failed:\n{message}")

    def install_target(self, requirement: str, target: Path) -> None:
        """Install one wheel, without dependencies, into a plain directory."""
        command = (
            self.executable,
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
            *self.extra_args,
        )
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            message = without_noise(result.stderr)
            raise UvError(f"uv pip install --target failed:\n{message}")

    def create_venv(self, path: Path) -> str:
        """Create (or recreate) a virtual environment for the target interpreter."""
        command = (
            self.executable,
            "venv",
            "--quiet",
            "--clear",
            "--python",
            self.python,
        )
        result = subprocess.run(
            (*command, str(path)), capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            raise UvError(f"uv venv failed:\n{without_noise(result.stderr)}")
        return str(path / "bin" / "python")


def find_uv(explicit: str | None) -> str:
    if explicit is not None:
        return explicit
    found = shutil.which("uv")
    if found is None:
        raise UvError("uv not found on PATH (use --uv)")
    return found


def find_interpreter(uv: str, request: str | None) -> str:
    """The interpreter uv would use (respecting .venv and uv's settings)."""
    command: tuple[str, ...] = (uv, "python", "find")
    if request is not None:
        command = (*command, request)
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise UvError(f"uv python find failed:\n{without_noise(result.stderr)}")
    return result.stdout.strip()


def marker_environment(interpreter: str) -> Mapping[str, str]:
    """PEP 508 marker values of the target interpreter, not the running one."""
    result = subprocess.run(
        (interpreter, "-I", "-c", MARKER_ENVIRONMENT_SCRIPT),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise UvError(f"cannot query {interpreter}: {result.stderr.strip()}")
    raw = json.loads(result.stdout)
    return MappingProxyType({str(key): str(value) for key, value in raw.items()})
