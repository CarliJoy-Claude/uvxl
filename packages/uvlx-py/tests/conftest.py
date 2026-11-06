from collections.abc import Iterator
from pathlib import Path

import pytest

from helpers import FakeIndex, ServedFile, World, build_wheel


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    """Five wheels on a fresh fake index; uv isolated from the user's config."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("NETRC", raising=False)
    monkeypatch.setenv("UV_NO_CACHE", "1")
    for variable in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    wheel_dir = tmp_path / "wheels"
    wheel_dir.mkdir()
    wheels = {
        "app": build_wheel(
            wheel_dir, "app", "1.0", ["dep-a>=1", "dep-c; extra == 'x'"], extras=["x"]
        ),
        "dep-a": build_wheel(wheel_dir, "dep-a", "1.2", ["dep-b"]),
        "dep-b": build_wheel(wheel_dir, "dep-b", "2.0"),
        "dep-c": build_wheel(wheel_dir, "dep-c", "3.0"),
        "evil": build_wheel(wheel_dir, "evil", "9.9"),
    }
    index = FakeIndex()
    index.projects.update({name: [ServedFile(path)] for name, path in wheels.items()})
    index.start()
    try:
        yield World(index, tmp_path, wheels)
    finally:
        index.stop()
