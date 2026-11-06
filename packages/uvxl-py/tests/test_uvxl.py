"""``uvxl``: real uv, a fake index hosting the lock, the whole run flow."""

import hashlib
import json
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from helpers import UV, FakeIndex, ServedFile, World, build_wheel
from uvxl.cli import uvxl_main
from uvxl.fetch import FetchPolicy

pytestmark = pytest.mark.skipif(UV is None, reason="uv is not installed")

LOCK_PATH = "/+f/0a1/pylock.toml"


@pytest.fixture
def out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Where the test wheel's console script writes its arguments."""
    path = tmp_path / "script-out.txt"
    monkeypatch.setenv("UVXL_TEST_OUT", str(path))
    return path


def publish_app(world: World, *project_urls: str) -> None:
    """Replace the root on the index by one that links a lock."""
    directory = Path(tempfile.mkdtemp(prefix="published-", dir=world.directory))
    wheel = build_wheel(
        directory,
        "app",
        "1.0",
        ["dep-a>=1", "dep-c; extra == 'x'"],
        extras=["x"],
        project_urls=project_urls,
        console_script=True,
    )
    world.index.projects["app"] = [ServedFile(wheel)]


def host_lock(world: World, server: FakeIndex | None = None) -> str:
    """Serve the good lock; return its URL."""
    if server is None:
        server = world.index
    content = world.write_lock(world.good_entries()).read_bytes()
    server.extra_files[LOCK_PATH] = content
    return server.base() + LOCK_PATH


def lock_sha256(world: World) -> str:
    return hashlib.sha256(world.index.extra_files[LOCK_PATH]).hexdigest()


def uvxl(
    world: World,
    *arguments: str,
    policy: FetchPolicy | None = None,
) -> Result:
    if policy is None:
        policy = FetchPolicy(allow_http=True)
    return CliRunner().invoke(
        uvxl_main,
        [
            "--python",
            sys.executable,
            f"--uv-arg=--index-url={world.index.url}",
            "--cache-dir",
            str(world.directory / "uvxl-cache"),
            *arguments,
        ],
        obj=policy,
    )


def environments(world: World) -> tuple[Path, ...]:
    envs = world.directory / "uvxl-cache" / "envs"
    if not envs.exists():
        return ()
    return tuple(sorted(envs.iterdir()))


def installed(environment: Path) -> frozenset[str]:
    assert UV is not None
    listed = subprocess.run(
        [
            UV,
            "pip",
            "list",
            "--python",
            str(environment / "bin" / "python"),
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return frozenset(item["name"] for item in json.loads(listed.stdout))


def lock_requests(server: FakeIndex) -> list[str | None]:
    return [auth for path, auth in server.requests if path == LOCK_PATH]


# --------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------


def test_runs_the_command_in_a_locked_environment(world: World, out: Path) -> None:
    publish_app(world, f"Pylock, {host_lock(world)}")
    result = uvxl(world, "app", "--flag", "value")
    assert result.exit_code == 3, result.stderr  # the script's own exit code
    assert out.read_text() == "--flag value"
    (environment,) = environments(world)
    assert installed(environment) == {"app", "dep-a", "dep-b"}
    assert "mutable lock" in result.stderr
    assert "environment created" in result.stderr


def test_receipt_records_the_lock(world: World, out: Path) -> None:
    url = host_lock(world)
    publish_app(world, f"Pylock, {url}")
    uvxl(world, "app")
    (environment,) = environments(world)
    receipt = json.loads((environment / "uvxl-receipt.json").read_text())
    assert receipt["lock-url"] == url
    assert receipt["lock-sha256"] == lock_sha256(world)
    assert receipt["lock-mode"] == "mutable"
    assert receipt["fetched-at"].endswith("+00:00")
    assert receipt["root"] == {"name": "app", "version": "1.0"}
    cleaned = tomllib.loads((environment / "pylock.uvxl.toml").read_text())
    assert [p["name"] for p in cleaned["packages"]] == ["app", "dep-a", "dep-b"]


def test_second_run_reuses_the_environment(world: World, out: Path) -> None:
    publish_app(world, f"Pylock, {host_lock(world)}")
    uvxl(world, "app")
    result = uvxl(world, "app")
    assert result.exit_code == 3, result.stderr
    assert "environment reused" in result.stderr
    assert len(environments(world)) == 1
    assert len(lock_requests(world.index)) == 2  # the lock is fetched every run


def test_from_with_extras(world: World, out: Path) -> None:
    publish_app(world, f"Pylock, {host_lock(world)}")
    result = uvxl(world, "--from", "app[x]==1.0", "app", "a")
    assert result.exit_code == 3, result.stderr
    (environment,) = environments(world)
    assert installed(environment) == {"app", "dep-a", "dep-b", "dep-c"}


def test_missing_command_is_an_error(world: World, out: Path) -> None:
    publish_app(world, f"Pylock, {host_lock(world)}")
    result = uvxl(world, "--from", "app", "nope")
    assert result.exit_code == 1
    assert "provides no 'nope'" in result.stderr


def test_pinned_lock(world: World, out: Path) -> None:
    url = host_lock(world)
    publish_app(world, f"Pylock, {url}#sha256={lock_sha256(world)}")
    result = uvxl(world, "app")
    assert result.exit_code == 3, result.stderr
    assert "pinned lock" in result.stderr


# --------------------------------------------------------------------------
# Refusals: nothing is installed
# --------------------------------------------------------------------------


def test_root_without_pylock_url_fails(world: World, out: Path) -> None:
    publish_app(world)
    result = uvxl(world, "app")
    assert result.exit_code == 1
    assert "[M5]" in result.stderr
    assert environments(world) == ()


def test_pinned_hash_mismatch_fails_before_parsing(world: World, out: Path) -> None:
    url = host_lock(world)
    publish_app(world, f"Pylock, {url}#sha256={'0' * 64}")
    result = uvxl(world, "app")
    assert result.exit_code == 1
    assert "[F5]" in result.stderr
    assert environments(world) == ()


def test_unknown_fragment_is_refused(world: World, out: Path) -> None:
    publish_app(world, f"Pylock, {host_lock(world)}#future=1")
    result = uvxl(world, "app")
    assert result.exit_code == 1
    assert "[M4]" in result.stderr


def test_http_lock_url_is_refused_in_production(world: World, out: Path) -> None:
    publish_app(world, f"Pylock, {host_lock(world)}")
    result = uvxl(world, "app", policy=FetchPolicy())
    assert result.exit_code == 1
    assert "[M3]" in result.stderr


def test_missing_lock_is_a_fetch_error(world: World, out: Path) -> None:
    publish_app(world, f"Pylock, {world.index.base()}/nothing/pylock.toml")
    result = uvxl(world, "app")
    assert result.exit_code == 1
    assert "[F6]" in result.stderr
    assert "404" in result.stderr


def test_oversized_lock_is_refused(world: World, out: Path) -> None:
    publish_app(world, f"Pylock, {host_lock(world)}")
    result = uvxl(world, "app", policy=FetchPolicy(allow_http=True, max_size=100))
    assert result.exit_code == 1
    assert "[F4]" in result.stderr


def test_too_many_redirects(world: World, out: Path) -> None:
    world.index.redirects["/loop/pylock.toml"] = "/loop/pylock.toml"
    publish_app(world, f"Pylock, {world.index.base()}/loop/pylock.toml")
    result = uvxl(world, "app")
    assert result.exit_code == 1
    assert "redirects" in result.stderr


def test_lock_rejected_by_verify_and_clean(world: World, out: Path) -> None:
    bad = world.entry("dep-a", index="https://pypi.org/simple")
    content = world.write_lock(world.replaced("dep-a", bad)).read_bytes()
    world.index.extra_files[LOCK_PATH] = content
    publish_app(world, f"Pylock, {world.index.base()}{LOCK_PATH}")
    result = uvxl(world, "app")
    assert result.exit_code == 1
    assert "[C5.3]" in result.stderr
    assert environments(world) == ()


def test_redirects_are_followed(world: World, out: Path) -> None:
    target = host_lock(world)
    world.index.redirects["/moved/pylock.toml"] = target
    publish_app(world, f"Pylock, {world.index.base()}/moved/pylock.toml")
    result = uvxl(world, "app")
    assert result.exit_code == 3, result.stderr
    assert len(lock_requests(world.index)) == 1


def test_root_with_several_wheels_keeps_all_served_wheels(
    world: World, out: Path
) -> None:
    """uv chooses the root wheel; C7 binds the version, not one file."""
    publish_app(world, f"Pylock, {host_lock(world)}")
    (first,) = world.index.projects["app"]
    second = build_wheel(
        first.path.parent,
        "app",
        "1.0",
        ["dep-a>=1"],
        tag="py2.py3-none-any",
        project_urls=(f"Pylock, {world.index.base()}{LOCK_PATH}",),
        console_script=True,
    )
    world.index.projects["app"].append(ServedFile(second))
    result = uvxl(world, "app")
    assert result.exit_code == 3, result.stderr
    (environment,) = environments(world)
    cleaned = tomllib.loads((environment / "pylock.uvxl.toml").read_text())
    (root,) = (p for p in cleaned["packages"] if p["name"] == "app")
    assert sorted(wheel["name"] for wheel in root["wheels"]) == sorted(
        (first.path.name, second.name)
    )
    for wheel in root["wheels"]:
        data = (first.path.parent / wheel["name"]).read_bytes()
        assert wheel["hashes"] == {"sha256": hashlib.sha256(data).hexdigest()}


def test_options_after_the_command_belong_to_the_command(
    world: World, out: Path
) -> None:
    publish_app(world, f"Pylock, {host_lock(world)}")
    result = uvxl(world, "app", "--help", "--python", "x")
    assert result.exit_code == 3, result.stderr
    assert out.read_text() == "--help --python x"


# --------------------------------------------------------------------------
# C8: uv enforces the hashes of the cleaned lock
# --------------------------------------------------------------------------


def serve_twin(world: World, real_salt: str, twin_salt: str) -> None:
    """Advertise one dep-b wheel but serve another valid wheel's bytes."""
    for directory in ("real", "evil-twin"):
        (world.directory / directory).mkdir()
    real = build_wheel(world.directory / "real", "dep-b", "2.0", salt=real_salt)
    twin = build_wheel(world.directory / "evil-twin", "dep-b", "2.0", salt=twin_salt)
    world.index.projects["dep-b"] = [ServedFile(real, replacement=twin)]


def test_tampered_download_fails_the_hash_check(world: World, out: Path) -> None:
    # Same size, different bytes: uv must get to the hash check.
    serve_twin(world, real_salt="real", twin_salt="twin")
    publish_app(world, f"Pylock, {host_lock(world)}")
    result = uvxl(world, "app")
    assert result.exit_code == 1
    assert "hash" in result.stderr.lower()
    assert not out.exists()


def test_tampered_download_of_other_size_fails_the_install(
    world: World, out: Path
) -> None:
    # uv 0.13 compares the locked size first and fails before hashing.
    serve_twin(world, real_salt="", twin_salt="twin")
    publish_app(world, f"Pylock, {host_lock(world)}")
    result = uvxl(world, "app")
    assert result.exit_code == 1
    assert "dep-b" in result.stderr
    assert not out.exists()
