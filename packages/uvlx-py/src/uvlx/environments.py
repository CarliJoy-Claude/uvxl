"""Cached environments built from cleaned locks, with a receipt (P1).

An environment is keyed by the hash of the cleaned lock and the target
interpreter, so a run with the same verified packages reuses it. The receipt
is written last: without one, the environment is created again.
"""

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from uvlx.fetch import FetchedLock
from uvlx.uv import Uv
from uvlx.wheel import RootWheel

RECEIPT_NAME: Final = "uvlx-receipt.json"
LOCK_NAME: Final = "pylock.uvlx.toml"


@dataclass(frozen=True, slots=True)
class LockedEnvironment:
    path: Path
    created: bool

    def executable(self, command: str) -> Path:
        return self.path / "bin" / command


def default_cache_dir() -> Path:
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "uvlx"
    return Path.home() / ".cache" / "uvlx"


def ensure_environment(
    uv: Uv,
    cleaned_lock: str,
    fetched: FetchedLock,
    root: RootWheel,
    cache_dir: Path,
) -> LockedEnvironment:
    """Reuse the environment for this cleaned lock, or build it (C8)."""
    key = hashlib.sha256(f"{uv.python}\n{cleaned_lock}".encode()).hexdigest()
    path = cache_dir / "envs" / key[:32]
    if (path / RECEIPT_NAME).is_file():
        return LockedEnvironment(path, created=False)
    python = uv.create_venv(path)
    lock = path / LOCK_NAME
    lock.write_text(cleaned_lock, encoding="utf-8")
    Uv(uv.executable, python, uv.extra_args).install(lock)
    receipt = {  # P1: which lock built this environment
        "lock-url": fetched.lock_url.url,
        "lock-mode": fetched.lock_url.mode,
        "lock-sha256": fetched.sha256,
        "fetched-at": fetched.fetched_at,
        "root": {"name": root.name, "version": str(root.version)},
    }
    (path / RECEIPT_NAME).write_text(json.dumps(receipt, indent=2) + "\n")
    return LockedEnvironment(path, created=True)
