"""``pylock-verify``: only allowed entries in a lock (C2, C3, N1)."""

from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from helpers import World
from uvxl.cli import verify_main


def verify(lock: Path, package: str = "app") -> Result:
    return CliRunner().invoke(verify_main, [str(lock), package])


def test_unmodified_uv_export_style_lock_is_ok(world: World) -> None:
    result = verify(world.write_lock(world.good_entries()))
    assert result.exit_code == 0, result.stderr
    assert "ok: 5 entries" in result.stdout


def test_package_name_is_normalized(world: World) -> None:
    result = verify(world.write_lock(world.good_entries()), "App")
    assert result.exit_code == 0, result.stderr


@pytest.mark.parametrize("source", ["vcs", "directory", "archive"])
def test_other_sources_are_rejected(world: World, source: str) -> None:
    entry = {"name": "dep-a", source: {"url": "https://evil.invalid/x"}}
    result = verify(world.write_lock(world.replaced("dep-a", entry)))
    assert result.exit_code == 1
    assert "[C3]" in result.stderr


def test_directory_for_another_package_is_rejected(world: World) -> None:
    result = verify(world.write_lock(world.good_entries()), "dep-a")
    assert result.exit_code == 1
    assert "[C3]" in result.stderr


def test_two_directory_entries_for_the_package_are_rejected(world: World) -> None:
    root = {"name": "app", "directory": {"path": "."}}
    result = verify(world.write_lock((*world.good_entries(), root)))
    assert result.exit_code == 1
    assert "[C2]" in result.stderr


def test_wheel_without_hashes_is_rejected(world: World) -> None:
    entry = world.entry("dep-b", wheels=[{"name": "x.whl"}])
    result = verify(world.write_lock(world.replaced("dep-b", entry)))
    assert result.exit_code == 1
    assert "[C3]" in result.stderr


def test_entry_without_index_is_rejected(world: World) -> None:
    entry = {k: v for k, v in world.entry("dep-b").items() if k != "index"}
    result = verify(world.write_lock(world.replaced("dep-b", entry)))
    assert result.exit_code == 1
    assert "[C3]" in result.stderr


def test_lock_file_name_must_match_pep_751(world: World) -> None:
    result = verify(world.write_lock(world.good_entries(), name="lock.toml"))
    assert result.exit_code == 1
    assert "[N1]" in result.stderr
