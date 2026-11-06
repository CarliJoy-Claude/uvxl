"""Paths inside a virtual environment, on POSIX and Windows alike.

``sysconfig``'s ``venv`` scheme gives ``bin`` or ``Scripts``, and ``EXE`` the
executable suffix (``.exe`` on Windows), so there is no platform branch here.
"""

import sysconfig
from pathlib import Path


def scripts_dir(environment: Path) -> Path:
    """The directory with the environment's executables."""
    base = str(environment)
    return Path(
        sysconfig.get_path(
            "scripts", scheme="venv", vars={"base": base, "platbase": base}
        )
    )


def executable(environment: Path, name: str) -> Path:
    """An executable of the environment, like ``python`` or a console script."""
    suffix = sysconfig.get_config_var("EXE") or ""
    return scripts_dir(environment) / f"{name}{suffix}"
