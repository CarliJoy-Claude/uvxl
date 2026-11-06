"""Running the uv CLI, and querying the target interpreter.

uv has no Python API, so the PoC drives the CLI. Constraints go to uv through
a pipe passed as ``/dev/fd/N`` and never touch the disk. POSIX only.
"""

import json
import os
import shutil
import subprocess
import threading
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


def _feed_pipe(fd: int, data: bytes) -> None:
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]
    except BrokenPipeError:
        pass  # uv exited early; its error is reported through the exit code
    finally:
        os.close(fd)


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
        """Run ``uv pip compile -`` with constraints passed through a pipe."""
        read_fd, write_fd = os.pipe()
        command = (
            self.executable,
            "pip",
            "compile",
            "-",
            "--constraints",
            f"/dev/fd/{read_fd}",
            "--python",
            self.python,
            "--no-header",
            *options,
            *self.extra_args,
        )
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                pass_fds=(read_fd,),
                text=True,
            )
        except OSError as error:
            os.close(write_fd)
            raise UvError(f"cannot run {self.executable}: {error}") from error
        finally:
            os.close(read_fd)
        writer = threading.Thread(
            target=_feed_pipe, args=(write_fd, constraints.encode()), daemon=True
        )
        writer.start()
        stdout, stderr = process.communicate(requirement + "\n")
        writer.join()
        stderr = without_noise(stderr)
        if process.returncode != 0:
            if "No solution found" in stderr:
                raise LockRejected(
                    "[C5] uv finds no resolution within the lock's pins:\n" + stderr
                )
            raise UvError(f"uv pip compile failed:\n{stderr}")
        return CompileOutput(stdout, stderr)

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
        """Create a virtual environment for the target interpreter."""
        command = (self.executable, "venv", "--quiet", "--python", self.python)
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
