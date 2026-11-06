"""The ``pylock-verify-clean`` command line, including installation (C8)."""

import json
import subprocess
import sys
import tomllib

import pytest
from click.testing import CliRunner, Result

from helpers import UV, ServedFile, World, build_wheel
from uvlx.cli import verify_clean_main

pytestmark = pytest.mark.skipif(UV is None, reason="uv is not installed")


def invoke(arguments: list[str]) -> Result:
    return CliRunner().invoke(verify_clean_main, arguments)


def cli(world: World, *extra: str) -> Result:
    assert UV is not None
    return invoke(
        [
            str(world.wheels["app"]),
            str(world.write_lock(world.good_entries())),
            "--python",
            sys.executable,
            "--uv",
            UV,
            *extra,
            "--",
            "--index-url",
            world.index.url,
        ]
    )


def test_cli_writes_cleaned_lock(world: World) -> None:
    output = world.directory / "pylock.cleaned.toml"
    result = cli(world, "-o", str(output))
    assert result.exit_code == 0, result.stderr
    document = tomllib.loads(output.read_text(encoding="utf-8"))
    assert [p["name"] for p in document["packages"]] == ["app", "dep-a", "dep-b"]
    assert "evil" in result.stderr


def test_cli_prints_cleaned_lock_to_stdout(world: World) -> None:
    result = cli(world)
    assert result.exit_code == 0, result.stderr
    document = tomllib.loads(result.stdout)
    assert [p["name"] for p in document["packages"]] == ["app", "dep-a", "dep-b"]


def test_cli_refuses_output_names_installers_refuse(world: World) -> None:
    result = cli(world, "-o", str(world.directory / "cleaned.toml"))
    assert result.exit_code == 2
    assert "PEP 751" in result.stderr


def test_cli_reports_rejection_with_exit_code_1(world: World) -> None:
    entries = world.replaced("dep-a", world.entry("dep-a", index="https://x.invalid"))
    result = invoke(
        [
            str(world.wheels["app"]),
            str(world.write_lock(entries)),
            "--python",
            sys.executable,
            "--",
            "--index-url",
            world.index.url,
        ]
    )
    assert result.exit_code == 1
    assert "[C5.3]" in result.stderr


def fresh_venv(world: World) -> str:
    assert UV is not None
    venv = world.directory / "venv"
    subprocess.run(
        [UV, "venv", "--python", sys.executable, str(venv)],
        check=True,
        capture_output=True,
    )
    return str(venv / "bin" / "python")


def install_cli(world: World, target: str) -> Result:
    assert UV is not None
    return invoke(
        [
            str(world.wheels["app"]),
            str(world.write_lock(world.good_entries())),
            "--python",
            target,
            "--uv",
            UV,
            "--install",
            "--",
            "--index-url",
            world.index.url,
        ]
    )


def test_cli_install_into_a_fresh_environment(world: World) -> None:
    assert UV is not None
    target = fresh_venv(world)
    result = install_cli(world, target)
    assert result.exit_code == 0, result.stderr
    listed = subprocess.run(
        [UV, "pip", "list", "--python", target, "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
    )
    installed = {item["name"] for item in json.loads(listed.stdout)}
    assert installed == {"app", "dep-a", "dep-b"}


def serve_twin(world: World, real_salt: str, twin_salt: str) -> None:
    """Advertise one dep-b wheel but serve another valid wheel's bytes."""
    for directory in ("real", "evil-twin"):
        (world.directory / directory).mkdir()
    real = build_wheel(world.directory / "real", "dep-b", "2.0", salt=real_salt)
    twin = build_wheel(world.directory / "evil-twin", "dep-b", "2.0", salt=twin_salt)
    world.index.projects["dep-b"] = [ServedFile(real, replacement=twin)]


def test_tampered_download_fails_the_hash_check(world: World) -> None:
    # Same size, different bytes: uv must get to the hash check.
    serve_twin(world, real_salt="real", twin_salt="twin")
    result = install_cli(world, fresh_venv(world))
    assert result.exit_code == 1
    assert "hash" in result.stderr.lower()


def test_tampered_download_of_other_size_fails_the_install(world: World) -> None:
    # uv 0.13 compares the locked size first and fails before hashing.
    serve_twin(world, real_salt="", twin_salt="twin")
    result = install_cli(world, fresh_venv(world))
    assert result.exit_code == 1
    assert "dep-b" in result.stderr
