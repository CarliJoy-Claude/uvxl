"""Cached environments built from cleaned locks, with a receipt (P1).

An environment is keyed by the hash of the cleaned lock and the target
interpreter, so a run with the same verified packages reuses it. The receipt
is written last: an environment without one is incomplete and is rebuilt.
Concurrent runs building the same environment are not handled (PoC).
"""

import hashlib
import json
import os
import shutil
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
    python: str
    created: bool

    def executable(self, command: str) -> Path:
        return self.path / "bin" / command

    @property
    def receipt(self) -> Path:
        return self.path / RECEIPT_NAME


def default_cache_dir() -> Path:
    explicit = os.environ.get("UVLX_CACHE_DIR")
    if explicit:
        return Path(explicit)
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "uvlx"
    return Path.home() / ".cache" / "uvlx"


def environment_key(cleaned_lock: str, interpreter: str) -> str:
    digest = hashlib.sha256(f"{interpreter}\n{cleaned_lock}".encode())
    return digest.hexdigest()[:32]


def receipt_document(
    fetched: FetchedLock, root: RootWheel, cleaned_lock: str, interpreter: str
) -> dict[str, object]:
    """Rule P1: enough to audit later which lock built this environment."""
    return {
        "lock-url": fetched.lock_url.url,
        "lock-final-url": fetched.final_url,
        "lock-mode": fetched.lock_url.mode,
        "lock-pinned": dict(fetched.lock_url.pinned),
        "lock-sha256": fetched.sha256,
        "fetched-at": fetched.fetched_at,
        "root": {
            "name": root.name,
            "version": str(root.version),
        },
        "cleaned-lock": LOCK_NAME,
        "cleaned-lock-sha256": hashlib.sha256(cleaned_lock.encode()).hexdigest(),
        "interpreter": interpreter,
    }


def ensure_environment(
    uv: Uv,
    cleaned_lock: str,
    fetched: FetchedLock,
    root: RootWheel,
    cache_dir: Path,
) -> LockedEnvironment:
    """Reuse the environment for this cleaned lock, or build it (C8)."""
    path = cache_dir / "envs" / environment_key(cleaned_lock, uv.python)
    python = str(path / "bin" / "python")
    if (path / RECEIPT_NAME).is_file():
        return LockedEnvironment(path, python, created=False)
    if path.exists():
        shutil.rmtree(path)  # an earlier attempt did not finish
    path.parent.mkdir(parents=True, exist_ok=True)
    python = uv.create_venv(path)
    lock = path / LOCK_NAME
    lock.write_text(cleaned_lock, encoding="utf-8")
    Uv(uv.executable, python, uv.extra_args).install(lock)
    receipt = receipt_document(fetched, root, cleaned_lock, uv.python)
    partial = path / (RECEIPT_NAME + ".partial")
    partial.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    partial.replace(path / RECEIPT_NAME)
    return LockedEnvironment(path, python, created=True)
