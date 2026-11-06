"""The Rust ``uvlx-rs`` binary against the same fake index (skipped if not built)."""

import base64
import os
import subprocess
import sys
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

import pytest

from helpers import FakeIndex, ServedFile, World, build_wheel

TARGET = Path(
    os.environ.get("CARGO_TARGET_DIR", Path(__file__).parents[2] / "uvlx-rs/target")
)
BINARY = TARGET / "debug" / "uvlx-rs"
LOCK_PATH = "/+f/0a1/pylock.toml"
AUTHORIZATION = "Basic " + base64.b64encode(b"user:secret").decode()

pytestmark = pytest.mark.skipif(not BINARY.exists(), reason="uvlx-rs is not built")


@pytest.fixture
def out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "script-out.txt"
    monkeypatch.setenv("UVLX_TEST_OUT", str(path))
    monkeypatch.setenv("UVLX_TEST_ALLOW_HTTP", "1")
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv-cache"))
    monkeypatch.delenv("UV_NO_CACHE")
    return path


def publish(
    world: World, host: FakeIndex, entries: Sequence[Mapping[str, object]] = ()
) -> None:
    """Host the lock on ``host`` and publish an app linking it."""
    lock_entries = entries or world.good_entries()
    host.extra_files[LOCK_PATH] = world.write_lock(lock_entries).read_bytes()
    directory = world.directory / f"app-{len(host.extra_files)}-{host.host}"
    directory.mkdir(exist_ok=True)
    app = build_wheel(
        directory,
        "app",
        "1.0",
        ["dep-a>=1", "dep-c; extra == 'x'"],
        extras=["x"],
        project_urls=(f"Pylock, {host.base()}{LOCK_PATH}",),
        console_script=True,
    )
    world.index.projects["app"] = [ServedFile(app)]


def uvlx_rs(
    world: World, *arguments: str, index_url: str | None = None
) -> subprocess.CompletedProcess[str]:
    if index_url is None:
        index_url = world.index.url
    return subprocess.run(
        [str(BINARY), "--python", sys.executable, "--index-url", index_url, *arguments],
        capture_output=True,
        text=True,
        check=False,
    )


def test_runs_the_command_in_a_locked_environment(world: World, out: Path) -> None:
    publish(world, world.index)
    result = uvlx_rs(world, "app", "--flag", "value")
    assert result.returncode == 3, result.stderr
    assert out.read_text() == "--flag value"
    assert "/simple/evil/" not in world.requested()


def test_second_run_reuses_the_environment(world: World, out: Path) -> None:
    publish(world, world.index)
    uvlx_rs(world, "app")
    result = uvlx_rs(world, "app")
    assert result.returncode == 3, result.stderr
    assert "Installed" not in result.stderr


def test_extras_via_from(world: World, out: Path) -> None:
    publish(world, world.index)
    result = uvlx_rs(world, "--from", "app[x]", "app")
    assert result.returncode == 3, result.stderr
    assert "Installed 4 packages" in result.stderr


@pytest.mark.parametrize(
    ("name", "replacement", "rule"),
    [
        ("dep-b", None, "[C5.1]"),
        ("dep-a", {"index": "https://pypi.org/simple"}, "[C5.3]"),
        (
            "dep-b",
            {"wheels": [{"name": "x.whl", "hashes": {"sha256": "0" * 64}}]},
            "[C5.4]",
        ),
    ],
)
def test_lock_rules(
    world: World, out: Path, name: str, replacement: dict[str, object] | None, rule: str
) -> None:
    entry = None
    if replacement is not None:
        entry = world.entry(name, **replacement)
    publish(world, world.index, world.replaced(name, entry))
    result = uvlx_rs(world, "app")
    assert result.returncode == 1
    assert rule in result.stderr
    assert not out.exists()


def test_yanked_dependency_is_refused(world: World, out: Path) -> None:
    world.index.projects["dep-b"][0].yanked = True
    publish(world, world.index)
    result = uvlx_rs(world, "app")
    assert result.returncode == 1
    assert "[C5.4]" in result.stderr
    assert "yanked" in result.stderr


@pytest.fixture
def foreign() -> Iterator[FakeIndex]:
    server = FakeIndex()
    server.start()
    yield server
    server.stop()


def test_lock_under_the_index_gets_its_credentials(world: World, out: Path) -> None:
    world.index.expected_authorization = AUTHORIZATION
    publish(world, world.index)
    index_url = f"http://user:secret@{world.index.host}/simple"
    result = uvlx_rs(world, "app", index_url=index_url)
    assert result.returncode == 3, result.stderr
    assert [a for p, a in world.index.requests if p == LOCK_PATH] == [AUTHORIZATION]


def test_foreign_lock_host_gets_no_credentials(
    world: World, foreign: FakeIndex, out: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    netrc = world.directory / ".netrc"
    netrc.write_text("machine 127.0.0.1 login user password secret\n")
    monkeypatch.setenv("NETRC", str(netrc))
    world.index.expected_authorization = AUTHORIZATION
    publish(world, foreign)
    index_url = f"http://user:secret@{world.index.host}/simple"
    result = uvlx_rs(world, "app", index_url=index_url)
    assert result.returncode == 3, result.stderr
    assert [a for p, a in foreign.requests if p == LOCK_PATH] == [None]
