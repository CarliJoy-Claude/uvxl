"""``uvlx``: real uv, a fake index hosting the lock, the whole run flow."""

import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from helpers import UV, FakeIndex, FakeKeyring, ServedFile, World, build_wheel
from uvlx.cli import uvlx_main
from uvlx.fetch import FetchPolicy

pytestmark = pytest.mark.skipif(UV is None, reason="uv is not installed")

LOCK_PATH = "/+f/0a1/pylock.toml"
AUTHORIZATION = "Basic " + base64.b64encode(b"user:secret").decode()


@pytest.fixture
def out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Where the test wheel's console script writes its arguments."""
    path = tmp_path / "script-out.txt"
    monkeypatch.setenv("UVLX_TEST_OUT", str(path))
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


def uvlx(
    world: World,
    *arguments: str,
    index_url: str | None = None,
    policy: FetchPolicy | None = None,
) -> Result:
    if index_url is None:
        index_url = world.index.url
    if policy is None:
        policy = FetchPolicy(allow_http=True)
    return CliRunner().invoke(
        uvlx_main,
        [
            "--python",
            sys.executable,
            "--index-url",
            index_url,
            "--cache-dir",
            str(world.directory / "uvlx-cache"),
            *arguments,
        ],
        obj=policy,
    )


def environments(world: World) -> tuple[Path, ...]:
    envs = world.directory / "uvlx-cache" / "envs"
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
    result = uvlx(world, "app", "--flag", "value")
    assert result.exit_code == 3, result.stderr  # the script's own exit code
    assert out.read_text() == "--flag value"
    (environment,) = environments(world)
    assert installed(environment) == {"app", "dep-a", "dep-b"}
    assert "mutable lock" in result.stderr
    assert "environment created" in result.stderr


def test_receipt_records_the_lock(world: World, out: Path) -> None:
    url = host_lock(world)
    publish_app(world, f"Pylock, {url}")
    uvlx(world, "app")
    (environment,) = environments(world)
    receipt = json.loads((environment / "uvlx-receipt.json").read_text())
    assert receipt["lock-url"] == url
    assert receipt["lock-sha256"] == lock_sha256(world)
    assert receipt["lock-mode"] == "mutable"
    assert receipt["fetched-at"].endswith("+00:00")
    assert receipt["root"]["filename"] == "app-1.0-py3-none-any.whl"
    cleaned = tomllib.loads((environment / "pylock.uvlx.toml").read_text())
    assert [p["name"] for p in cleaned["packages"]] == ["app", "dep-a", "dep-b"]


def test_second_run_reuses_the_environment(world: World, out: Path) -> None:
    publish_app(world, f"Pylock, {host_lock(world)}")
    uvlx(world, "app")
    result = uvlx(world, "app")
    assert result.exit_code == 3, result.stderr
    assert "environment reused" in result.stderr
    assert len(environments(world)) == 1
    assert len(lock_requests(world.index)) == 2  # the lock is fetched every run


def test_from_with_extras(world: World, out: Path) -> None:
    publish_app(world, f"Pylock, {host_lock(world)}")
    result = uvlx(world, "--from", "app[x]==1.0", "app", "a")
    assert result.exit_code == 3, result.stderr
    (environment,) = environments(world)
    assert installed(environment) == {"app", "dep-a", "dep-b", "dep-c"}


def test_missing_command_is_an_error(world: World, out: Path) -> None:
    publish_app(world, f"Pylock, {host_lock(world)}")
    result = uvlx(world, "--from", "app", "nope")
    assert result.exit_code == 2
    assert "no command 'nope'" in result.stderr


def test_pinned_lock(world: World, out: Path) -> None:
    url = host_lock(world)
    publish_app(world, f"Pylock, {url}#sha256={lock_sha256(world)}")
    result = uvlx(world, "app")
    assert result.exit_code == 3, result.stderr
    assert "pinned lock" in result.stderr


# --------------------------------------------------------------------------
# Refusals: nothing is installed
# --------------------------------------------------------------------------


def test_root_without_pylock_url_fails(world: World, out: Path) -> None:
    publish_app(world)
    result = uvlx(world, "app")
    assert result.exit_code == 1
    assert "[M5]" in result.stderr
    assert environments(world) == ()


def test_pinned_hash_mismatch_fails_before_parsing(world: World, out: Path) -> None:
    url = host_lock(world)
    publish_app(world, f"Pylock, {url}#sha256={'0' * 64}")
    result = uvlx(world, "app")
    assert result.exit_code == 1
    assert "[F5]" in result.stderr
    assert environments(world) == ()


def test_unknown_fragment_needs_the_override(world: World, out: Path) -> None:
    publish_app(world, f"Pylock, {host_lock(world)}#future=1")
    refused = uvlx(world, "app")
    assert refused.exit_code == 1
    assert "[M4]" in refused.stderr
    allowed = uvlx(world, "--allow-unknown-fragment", "app")
    assert allowed.exit_code == 3, allowed.stderr


def test_http_lock_url_is_refused_in_production(world: World, out: Path) -> None:
    publish_app(world, f"Pylock, {host_lock(world)}")
    result = uvlx(world, "app", policy=FetchPolicy())
    assert result.exit_code == 1
    assert "[M3]" in result.stderr


def test_missing_lock_is_a_fetch_error(world: World, out: Path) -> None:
    publish_app(world, f"Pylock, {world.index.base()}/nothing/pylock.toml")
    result = uvlx(world, "app")
    assert result.exit_code == 2
    assert "[F6]" in result.stderr
    assert "404" in result.stderr


def test_oversized_lock_is_refused(world: World, out: Path) -> None:
    publish_app(world, f"Pylock, {host_lock(world)}")
    result = uvlx(world, "app", policy=FetchPolicy(allow_http=True, max_size=100))
    assert result.exit_code == 2
    assert "[F4]" in result.stderr


def test_too_many_redirects(world: World, out: Path) -> None:
    world.index.redirects["/loop/pylock.toml"] = "/loop/pylock.toml"
    publish_app(world, f"Pylock, {world.index.base()}/loop/pylock.toml")
    result = uvlx(world, "app")
    assert result.exit_code == 2
    assert "redirects" in result.stderr


def test_lock_rejected_by_verify_and_clean(world: World, out: Path) -> None:
    bad = world.entry("dep-a", index="https://pypi.org/simple")
    content = world.write_lock(world.replaced("dep-a", bad)).read_bytes()
    world.index.extra_files[LOCK_PATH] = content
    publish_app(world, f"Pylock, {world.index.base()}{LOCK_PATH}")
    result = uvlx(world, "app")
    assert result.exit_code == 1
    assert "[C5.3]" in result.stderr
    assert environments(world) == ()


# --------------------------------------------------------------------------
# F3: credentials from keyring, only for a lock under a configured index
# --------------------------------------------------------------------------


@pytest.fixture
def foreign(world: World) -> Iterator[FakeIndex]:
    """A second origin (another port) that needs no credentials."""
    server = FakeIndex()
    server.start()
    yield server
    server.stop()


@pytest.fixture
def keyring(world: World, monkeypatch: pytest.MonkeyPatch) -> FakeKeyring:
    """A fake keyring on PATH; uv uses it too (subprocess provider)."""
    fake = FakeKeyring(world.directory / "keyring")
    monkeypatch.setenv("PATH", f"{fake.directory}:{os.environ['PATH']}")
    monkeypatch.setenv("UV_KEYRING_PROVIDER", "subprocess")
    return fake


def protected_index(world: World, keyring: FakeKeyring) -> str:
    """The index needs credentials; keyring has them for its host."""
    world.index.expected_authorization = AUTHORIZATION
    keyring.add(world.index.host, "user", "secret")
    return f"http://user@{world.index.host}/simple"


def test_lock_on_the_index_gets_keyring_credentials(
    world: World, keyring: FakeKeyring, out: Path
) -> None:
    index_url = protected_index(world, keyring)
    publish_app(world, f"Pylock, {host_lock(world)}")
    result = uvlx(world, "app", index_url=index_url)
    assert result.exit_code == 3, result.stderr
    assert lock_requests(world.index) == [AUTHORIZATION]
    # Like uv: the index URL first, then the host.
    assert ["get", world.index.url, "user"] in keyring.calls()
    assert ["get", world.index.host, "user"] in keyring.calls()


def test_index_without_user_name_asks_keyring_for_both(
    world: World, keyring: FakeKeyring, out: Path
) -> None:
    keyring.add(world.index.url, "user", "secret")
    publish_app(world, f"Pylock, {host_lock(world)}")
    result = uvlx(world, "app")
    assert result.exit_code == 3, result.stderr
    assert lock_requests(world.index) == [AUTHORIZATION]
    assert keyring.calls() == [["get", world.index.url, "--mode", "creds"]]


def test_foreign_lock_host_gets_no_credentials(
    world: World, foreign: FakeIndex, keyring: FakeKeyring, out: Path
) -> None:
    index_url = protected_index(world, keyring)
    keyring.add(foreign.host, "user", "secret")
    publish_app(world, f"Pylock, {host_lock(world, foreign)}")
    result = uvlx(world, "app", index_url=index_url)
    assert result.exit_code == 3, result.stderr
    assert lock_requests(foreign) == [None]
    assert not [call for call in keyring.calls() if foreign.host in call[1]]


def test_redirect_to_another_origin_drops_credentials(
    world: World, foreign: FakeIndex, keyring: FakeKeyring, out: Path
) -> None:
    index_url = protected_index(world, keyring)
    target = host_lock(world, foreign)
    world.index.redirects["/moved/pylock.toml"] = target
    publish_app(world, f"Pylock, {world.index.base()}/moved/pylock.toml")
    result = uvlx(world, "app", index_url=index_url)
    assert result.exit_code == 3, result.stderr
    moved = [auth for path, auth in world.index.requests if path.startswith("/moved")]
    assert moved == [AUTHORIZATION]
    assert lock_requests(foreign) == [None]


def test_root_with_several_wheels_binds_the_one_uv_installed(
    world: World, out: Path
) -> None:
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
    result = uvlx(world, "app")
    assert result.exit_code == 3, result.stderr
    (environment,) = environments(world)
    receipt = json.loads((environment / "uvlx-receipt.json").read_text())
    cleaned = tomllib.loads((environment / "pylock.uvlx.toml").read_text())
    (root,) = (p for p in cleaned["packages"] if p["name"] == "app")
    (wheel,) = root["wheels"]
    assert wheel["name"] == receipt["root"]["filename"]
    assert wheel["name"] in {first.path.name, second.name}
    expected = hashlib.sha256(
        (first.path.parent / wheel["name"]).read_bytes()
    ).hexdigest()
    assert wheel["hashes"] == {"sha256": expected}


def test_options_after_the_command_belong_to_the_command(
    world: World, out: Path
) -> None:
    publish_app(world, f"Pylock, {host_lock(world)}")
    result = uvlx(world, "app", "--help", "--python", "x")
    assert result.exit_code == 3, result.stderr
    assert out.read_text() == "--help --python x"
