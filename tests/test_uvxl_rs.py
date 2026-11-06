"""Behaviour only the Rust backend has: index credentials for the lock (F3).

The Python backend sends no credentials at all; the behaviour both
backends share is in ``test_backends.py``.
"""

import base64
from pathlib import Path

import pytest

from helpers import LOCK_PATH, UV, FakeIndex, World, lock_requests, publish, uvxl

pytestmark = pytest.mark.skipif(UV is None, reason="uv is not installed")

AUTHORIZATION = "Basic " + base64.b64encode(b"user:secret").decode()


def test_lock_under_the_index_gets_its_credentials(world: World, out: Path) -> None:
    world.index.expected_authorization = AUTHORIZATION
    publish(world)
    index_url = f"http://user:secret@{world.index.host}/simple"
    result = uvxl(world, "rust", "app", index_url=index_url)
    assert result.returncode == 3, result.stderr
    assert lock_requests(world.index) == [AUTHORIZATION]


def test_foreign_lock_host_gets_no_credentials(
    world: World, foreign: FakeIndex, out: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    netrc = world.directory / ".netrc"
    netrc.write_text("machine 127.0.0.1 login user password secret\n")
    monkeypatch.setenv("NETRC", str(netrc))
    world.index.expected_authorization = AUTHORIZATION
    publish(world, host=foreign)
    index_url = f"http://user:secret@{world.index.host}/simple"
    result = uvxl(world, "rust", "app", index_url=index_url)
    assert result.returncode == 3, result.stderr
    assert [a for p, a in foreign.requests if p == LOCK_PATH] == [None]


def test_python_backend_sends_no_credentials(world: World, out: Path) -> None:
    world.index.expected_authorization = AUTHORIZATION
    publish(world)
    index_url = f"http://user:secret@{world.index.host}/simple"
    result = uvxl(world, "python", "app", index_url=index_url)
    assert result.returncode == 1
    assert "[F6]" in result.stderr
    assert lock_requests(world.index) == [None]


def test_no_command_is_an_error(world: World, out: Path) -> None:
    result = uvxl(world, "rust")
    assert result.returncode == 1
    assert "no command given" in result.stderr


@pytest.mark.parametrize(
    ("served", "message"),
    [
        ({"metadata": False}, "no PEP 658 metadata"),
        ({"metadata_hash": "md5"}, "no usable hash to verify"),
        ({"hashes": False}, "[C7] the index states no hash"),
    ],
)
def test_root_served_by_a_sloppy_index(
    world: World, out: Path, served: dict[str, object], message: str
) -> None:
    publish(world)
    (app,) = world.index.projects["app"]
    for field, value in served.items():
        setattr(app, field, value)
    result = uvxl(world, "rust", "app")
    assert result.returncode == 1
    assert message in result.stderr


def test_invalid_pinned_digest_is_m4_in_rust(world: World, out: Path) -> None:
    # The Python backend reads any digest and fails at F5 instead.
    url = publish(world)
    publish(world, f"Pylock, {url}#sha256=zz")
    result = uvxl(world, "rust", "app")
    assert result.returncode == 1
    assert "[M4]" in result.stderr
