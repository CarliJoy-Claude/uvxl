"""Behaviour only the Python backend has: receipt, cleaned lock, status line.

Runs ``uvxl-py`` in-process (click's ``CliRunner``); the behaviour both
backends share is in ``test_backends.py``.
"""

import hashlib
import json
import sys
import tomllib
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from helpers import LOCK_PATH, UV, ServedFile, World, build_wheel, publish
from uvxl_py.cli import uvxl_main

pytestmark = pytest.mark.skipif(UV is None, reason="uv is not installed")


def uvxl_py(world: World, *arguments: str) -> Result:
    return CliRunner().invoke(
        uvxl_main,
        [
            "--python",
            sys.executable,
            "--index-url",
            world.index.url,
            "--cache-dir",
            str(world.directory / "uv-cache"),
            "--allow-insecure-host",
            "127.0.0.1",
            *arguments,
        ],
    )


def environments(world: World) -> tuple[Path, ...]:
    envs = world.directory / "uv-cache" / "uvxl" / "envs"
    if not envs.exists():
        return ()
    return tuple(sorted(envs.iterdir()))


def test_receipt_records_the_lock(world: World, out: Path) -> None:
    url = publish(world)
    result = uvxl_py(world, "app")
    assert result.exit_code == 3, result.stderr
    assert "environment created" in result.stderr
    (environment,) = environments(world)
    receipt = json.loads((environment / "uvxl-receipt.json").read_text())
    assert receipt["lock-url"] == url
    lock = world.index.extra_files[LOCK_PATH]
    assert receipt["lock-sha256"] == hashlib.sha256(lock).hexdigest()
    assert receipt["lock-mode"] == "mutable"
    assert receipt["fetched-at"].endswith("+00:00")
    assert receipt["root"] == {"name": "app", "version": "1.0"}
    cleaned = tomllib.loads((environment / "pylock.uvxl.toml").read_text())
    assert [p["name"] for p in cleaned["packages"]] == ["app", "dep-a", "dep-b"]


def test_second_run_says_reused(world: World, out: Path) -> None:
    publish(world)
    uvxl_py(world, "app")
    result = uvxl_py(world, "app")
    assert result.exit_code == 3, result.stderr
    assert "environment reused" in result.stderr


def test_cleaned_lock_keeps_all_served_root_wheels(world: World, out: Path) -> None:
    """uv chooses the root wheel; C7 binds the version, not one file."""
    url = publish(world)
    (first,) = world.index.projects["app"]
    second = build_wheel(
        first.path.parent,
        "app",
        "1.0",
        ["dep-a>=1"],
        tag="py2.py3-none-any",
        project_urls=(f"Pylock, {url}",),
        console_script=True,
    )
    world.index.projects["app"].append(ServedFile(second))
    result = uvxl_py(world, "app")
    assert result.exit_code == 3, result.stderr
    (environment,) = environments(world)
    cleaned = tomllib.loads((environment / "pylock.uvxl.toml").read_text())
    (root,) = (p for p in cleaned["packages"] if p["name"] == "app")
    assert root.get("index") is None
    assert sorted(wheel["name"] for wheel in root["wheels"]) == sorted(
        (first.path.name, second.name)
    )


@pytest.mark.parametrize(
    ("requirement", "message"),
    [
        ("app[", "invalid requirement"),
        ("app @ https://x.invalid/app.whl", "only a name, extras and a version"),
    ],
)
def test_invalid_from_is_an_error(
    world: World, out: Path, requirement: str, message: str
) -> None:
    result = uvxl_py(world, "--from", requirement, "app")
    assert result.exit_code == 1
    assert message in result.stderr


def test_without_python_option_uv_picks_the_interpreter(
    world: World, out: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The test's own virtual environment is what uv finds first.
    monkeypatch.setenv("VIRTUAL_ENV", sys.prefix)
    publish(world)
    result = CliRunner().invoke(
        uvxl_main,
        [
            "--index-url",
            world.index.url,
            "--cache-dir",
            str(world.directory / "uv-cache"),
            "--allow-insecure-host",
            "127.0.0.1",
            "app",
        ],
    )
    assert result.exit_code == 3, result.stderr


def test_uv_missing_from_path_is_an_error(
    world: World, out: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(world.directory / "empty"))
    result = uvxl_py(world, "app")
    assert result.exit_code == 1
    assert "uv not found on PATH" in result.stderr
