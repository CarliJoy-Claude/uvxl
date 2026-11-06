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
