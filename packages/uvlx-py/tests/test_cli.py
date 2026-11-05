"""The ``pylock-verify-clean`` command line, including installation (C8)."""

import json
import subprocess
import sys
import tomllib

import pytest

from helpers import UV, ServedFile, World, build_wheel
from uvlx.cli import verify_clean_main

pytestmark = pytest.mark.skipif(UV is None, reason="uv is not installed")


def cli(world: World, *extra: str) -> int:
    assert UV is not None
    return verify_clean_main(
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


def test_cli_writes_cleaned_lock(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    output = world.directory / "pylock.cleaned.toml"
    assert cli(world, "-o", str(output)) == 0
    document = tomllib.loads(output.read_text(encoding="utf-8"))
    assert [p["name"] for p in document["packages"]] == ["app", "dep-a", "dep-b"]
    assert "evil" in capsys.readouterr().err


def test_cli_refuses_output_names_installers_refuse(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli(world, "-o", str(world.directory / "cleaned.toml")) == 2
    assert "PEP 751" in capsys.readouterr().err


def fresh_venv(world: World) -> str:
    assert UV is not None
    venv = world.directory / "venv"
    subprocess.run(
        [UV, "venv", "--python", sys.executable, str(venv)],
        check=True,
        capture_output=True,
    )
    return str(venv / "bin" / "python")


def install_cli(world: World, target: str) -> int:
    assert UV is not None
    return verify_clean_main(
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
    assert install_cli(world, target) == 0
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


def test_tampered_download_fails_the_hash_check(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    # Same size, different bytes: uv must get to the hash check.
    serve_twin(world, real_salt="real", twin_salt="twin")
    assert install_cli(world, fresh_venv(world)) == 2
    assert "hash" in capsys.readouterr().err.lower()


def test_tampered_download_of_other_size_fails_the_install(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    # uv 0.13 compares the locked size first and fails before hashing.
    serve_twin(world, real_salt="", twin_salt="twin")
    assert install_cli(world, fresh_venv(world)) == 2
    assert "dep-b" in capsys.readouterr().err
