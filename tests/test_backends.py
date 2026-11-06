"""Every behaviour both backends share, run through ``uvxl`` with each backend.

Assertions use only what both produce: exit codes, the test script's output,
rule IDs on stderr, the fake index's request log, and the environment path
from the status line.
"""

import hashlib
import tempfile
from pathlib import Path

import pytest

from helpers import (
    LOCK_PATH,
    UV,
    FakeIndex,
    ServedFile,
    World,
    build_wheel,
    environment_of,
    installed,
    lock_requests,
    publish,
    uvxl,
)

pytestmark = pytest.mark.skipif(UV is None, reason="uv is not installed")


@pytest.fixture(params=["python", "rust"])
def backend(request: pytest.FixtureRequest) -> str:
    return str(request.param)


# --------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------


def test_runs_the_command_in_a_locked_environment(
    world: World, backend: str, out: Path
) -> None:
    url = publish(world)
    result = uvxl(world, backend, "app", "--flag", "value")
    assert result.returncode == 3, result.stderr  # the script's own exit code
    assert out.read_text() == "--flag value"
    assert f"mutable lock {url}" in result.stderr
    assert installed(environment_of(result)) == {"app", "dep-a", "dep-b"}
    assert "/simple/evil/" not in world.requested()  # C6


def test_environment_is_in_the_cache_dir_and_reused(
    world: World, backend: str, out: Path
) -> None:
    publish(world)
    first = uvxl(world, backend, "app")
    second = uvxl(world, backend, "app")
    assert second.returncode == 3, second.stderr
    environment = environment_of(first)
    assert environment == environment_of(second)
    # Resolved: on macOS /var is a symlink to /private/var.
    cache = (world.directory / "uv-cache").resolve()
    assert environment.resolve().is_relative_to(cache)
    assert len(lock_requests(world.index)) == 2  # the lock is fetched every run


def test_from_with_extras(world: World, backend: str, out: Path) -> None:
    publish(world)
    result = uvxl(world, backend, "--from", "app[x]==1.0", "app", "a")
    assert result.returncode == 3, result.stderr
    assert out.read_text() == "a"
    assert installed(environment_of(result)) == {"app", "dep-a", "dep-b", "dep-c"}


def test_options_after_the_command_belong_to_the_command(
    world: World, backend: str, out: Path
) -> None:
    publish(world)
    result = uvxl(world, backend, "app", "--help", "--python", "x")
    assert result.returncode == 3, result.stderr
    assert out.read_text() == "--help --python x"


def test_missing_command_is_an_error(world: World, backend: str, out: Path) -> None:
    publish(world)
    result = uvxl(world, backend, "--from", "app", "nope")
    assert result.returncode == 1
    assert "provides no 'nope'" in result.stderr.replace('"', "'")


def test_pinned_lock(world: World, backend: str, out: Path) -> None:
    url = publish(world)
    digest = hashlib.sha256(world.index.extra_files[LOCK_PATH]).hexdigest()
    publish(world, f"Pylock, {url}#sha256={digest}")
    result = uvxl(world, backend, "app")
    assert result.returncode == 3, result.stderr
    assert f"pinned lock {url}" in result.stderr


def test_redirects_are_followed(world: World, backend: str, out: Path) -> None:
    url = publish(world)
    world.index.redirects["/moved/pylock.toml"] = url
    publish(world, f"Pylock, {world.index.base()}/moved/pylock.toml")
    result = uvxl(world, backend, "app")
    assert result.returncode == 3, result.stderr


def test_root_with_several_wheels(world: World, backend: str, out: Path) -> None:
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
    result = uvxl(world, backend, "app")
    assert result.returncode == 3, result.stderr


# --------------------------------------------------------------------------
# Refusals: nothing runs
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("project_urls", "rule"),
    [
        ((), "[M5]"),
        (
            (
                "Pylock, http://127.0.0.1/pylock.toml",
                "pylock, http://127.0.0.1/pylock.b.toml",
            ),
            "[M2]",
        ),
        (("Pylock, http://127.0.0.1/pylock.toml#future=1",), "[M4]"),
        (("Pylock, http://127.0.0.1/lock.toml",), "[N1]"),
        (("Pylock, https://",), "[M3]"),
    ],
)
def test_pylock_url_rules(
    world: World, backend: str, out: Path, project_urls: tuple[str, ...], rule: str
) -> None:
    publish(world)
    if project_urls:
        publish(world, *project_urls)
    else:
        directory = Path(tempfile.mkdtemp(dir=world.directory))
        plain = build_wheel(directory, "app", "1.0", ["dep-a"], console_script=True)
        world.index.projects["app"] = [ServedFile(plain)]
    result = uvxl(world, backend, "app")
    assert result.returncode == 1
    assert rule in result.stderr
    assert not out.exists()


def test_http_lock_url_needs_allow_insecure_host(
    world: World, backend: str, out: Path
) -> None:
    publish(world)
    result = uvxl(world, backend, "app", insecure=False)
    assert result.returncode == 1
    assert "[M3]" in result.stderr
    assert not out.exists()


def test_pinned_hash_mismatch(world: World, backend: str, out: Path) -> None:
    url = publish(world)
    publish(world, f"Pylock, {url}#sha256={'0' * 64}")
    result = uvxl(world, backend, "app")
    assert result.returncode == 1
    assert "[F5]" in result.stderr
    assert not out.exists()


@pytest.mark.parametrize(
    ("setup", "rule"),
    [
        ("missing", "[F6]"),
        ("oversized", "[F4]"),
        ("redirect-loop", "[F4]"),
        ("unreachable", "[F6]"),
        ("invalid-toml", "invalid TOML"),
    ],
)
def test_fetch_rules(
    world: World, backend: str, out: Path, setup: str, rule: str
) -> None:
    publish(world)
    if setup == "missing":
        publish(world, f"Pylock, {world.index.base()}/nothing/pylock.toml")
    elif setup == "oversized":
        world.index.extra_files[LOCK_PATH] = b"#" * (1024 * 1024 + 1)
    elif setup == "unreachable":
        publish(world, "Pylock, http://127.0.0.1:9/pylock.toml")
    elif setup == "invalid-toml":
        world.index.extra_files[LOCK_PATH] = b"lock-version = "
    else:
        world.index.redirects["/loop/pylock.toml"] = "/loop/pylock.toml"
        publish(world, f"Pylock, {world.index.base()}/loop/pylock.toml")
    result = uvxl(world, backend, "app")
    assert result.returncode == 1
    assert rule in result.stderr
    assert not out.exists()


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
        ("dep-a", {"version": "0.5"}, "[C5"),
    ],
)
def test_lock_rules(
    world: World,
    backend: str,
    out: Path,
    name: str,
    replacement: dict[str, object] | None,
    rule: str,
) -> None:
    entry = None
    if replacement is not None:
        entry = world.entry(name, **replacement)
    publish(world, entries=world.replaced(name, entry))
    result = uvxl(world, backend, "app")
    assert result.returncode == 1
    assert rule in result.stderr
    assert not out.exists()


def test_lock_with_a_vcs_entry_is_rejected(
    world: World, backend: str, out: Path
) -> None:
    extra = {"name": "zzz", "vcs": {"type": "git", "url": "https://x.invalid/r"}}
    publish(world, entries=(*world.good_entries(), extra))
    result = uvxl(world, backend, "app")
    assert result.returncode == 1
    assert "[C3]" in result.stderr


def test_yanked_dependency_is_refused(world: World, backend: str, out: Path) -> None:
    world.index.projects["dep-b"][0].yanked = True
    publish(world)
    result = uvxl(world, backend, "app")
    assert result.returncode == 1
    assert "[C5.4]" in result.stderr
    assert "yanked" in result.stderr


@pytest.mark.parametrize(
    "option",
    [
        ("--with", "dep-c"),
        ("--with-editable", "."),
        ("--with-requirements", "requirements.txt"),
        ("--constraints", "constraints.txt"),
        ("--overrides", "overrides.txt"),
    ],
)
def test_options_that_bypass_the_lock_are_refused(
    world: World, backend: str, out: Path, option: tuple[str, str]
) -> None:
    publish(world)
    result = uvxl(world, backend, *option, "app")
    assert result.returncode == 1
    assert "not supported with a locked run" in result.stderr
    assert not out.exists()


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


@pytest.mark.parametrize(
    ("real_salt", "twin_salt"),
    [("real", "twin"), ("", "twin")],  # same size (hash check), other size
)
def test_tampered_download_fails_the_install(
    world: World, backend: str, out: Path, real_salt: str, twin_salt: str
) -> None:
    serve_twin(world, real_salt, twin_salt)
    publish(world)
    result = uvxl(world, backend, "app")
    assert result.returncode == 1
    assert "dep-b" in result.stderr
    assert not out.exists()


# --------------------------------------------------------------------------
# Pass-through uvx options
# --------------------------------------------------------------------------


def test_no_index_reaches_uv(world: World, backend: str, out: Path) -> None:
    publish(world)
    result = uvxl(world, backend, "--no-index", "app")
    assert result.returncode == 1
    assert not world.requested()


def test_quiet_is_accepted(world: World, backend: str, out: Path) -> None:
    publish(world)
    result = uvxl(world, backend, "-q", "app")
    assert result.returncode == 3, result.stderr


def test_exclude_newer_reaches_uv(world: World, backend: str, out: Path) -> None:
    # The fake index states no upload times, so uv excludes every file.
    publish(world)
    result = uvxl(world, backend, "--exclude-newer", "2100-01-01", "app")
    assert result.returncode == 1
    assert "no versions of app" in result.stderr


def test_lock_on_another_host(
    world: World, backend: str, out: Path, foreign: FakeIndex
) -> None:
    publish(world, host=foreign)
    result = uvxl(world, backend, "app")
    assert result.returncode == 3, result.stderr
    assert lock_requests(foreign) == [None]


# --------------------------------------------------------------------------
# The lock's own checks (C1 to C4) through both backends
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "top_level",
    [
        {"lock-version": "2.0"},
        {"environments": ["sys_platform == 'nonexistent'"]},
        {"requires-python": "<3"},
        {"requires-python": "not a specifier"},
    ],
)
def test_lock_header_rules(
    world: World, backend: str, out: Path, top_level: dict[str, object]
) -> None:
    publish(world)
    lock = world.write_lock(world.good_entries(), top_level=top_level)
    world.index.extra_files[LOCK_PATH] = lock.read_bytes()
    result = uvxl(world, backend, "app")
    assert result.returncode == 1
    assert "[C1]" in result.stderr


def test_lock_header_that_covers_this_environment(
    world: World, backend: str, out: Path
) -> None:
    publish(world)
    lock = world.write_lock(
        world.good_entries(),
        top_level={
            "environments": ["python_version >= '3'"],
            "requires-python": ">=3.8",
        },
    )
    world.index.extra_files[LOCK_PATH] = lock.read_bytes()
    result = uvxl(world, backend, "app")
    assert result.returncode == 3, result.stderr


@pytest.mark.parametrize(
    ("change", "rule"),
    [
        ({"wheels": []}, "[C3]"),
        ({"wheels": [{"name": "x.whl"}]}, "[C3]"),
        ({"version": None}, "[C3]"),
        ({"index": None}, "[C3]"),
        ({"marker": "python_version >>> '3'"}, "invalid marker"),
    ],
)
def test_lock_entry_rules(
    world: World, backend: str, out: Path, change: dict[str, object], rule: str
) -> None:
    entry = {
        k: v for k, v in {**world.entry("dep-b"), **change}.items() if v is not None
    }
    publish(world, entries=world.replaced("dep-b", entry))
    result = uvxl(world, backend, "app")
    assert result.returncode == 1
    assert rule in result.stderr


def test_two_applicable_entries_are_ambiguous(
    world: World, backend: str, out: Path
) -> None:
    publish(world, entries=(*world.good_entries(), world.entry("dep-b")))
    result = uvxl(world, backend, "app")
    assert result.returncode == 1
    assert "[C4]" in result.stderr


def test_more_hash_algorithms_in_the_lock_are_fine(
    world: World, backend: str, out: Path
) -> None:
    data = world.wheels["dep-b"].read_bytes()
    hashes = {
        algorithm: hashlib.new(algorithm, data).hexdigest()
        for algorithm in ("sha256", "sha384", "sha512")
    }
    wheel = {"name": world.wheels["dep-b"].name, "hashes": {**hashes, "md5": "x"}}
    publish(
        world, entries=world.replaced("dep-b", world.entry("dep-b", wheels=[wheel]))
    )
    result = uvxl(world, backend, "app")
    assert result.returncode == 3, result.stderr


def test_lock_index_that_is_no_url_is_no_configured_index(
    world: World, backend: str, out: Path
) -> None:
    entry = world.entry("dep-a", index="not a url")
    publish(world, entries=world.replaced("dep-a", entry))
    result = uvxl(world, backend, "app")
    assert result.returncode == 1
    assert "[C5.3]" in result.stderr


@pytest.mark.parametrize("algorithm", ["sha384", "sha512"])
def test_pinned_lock_with_other_algorithms(
    world: World, backend: str, out: Path, algorithm: str
) -> None:
    url = publish(world)
    digest = hashlib.new(algorithm, world.index.extra_files[LOCK_PATH]).hexdigest()
    publish(world, f"Pylock, {url}#{algorithm}={digest}")
    result = uvxl(world, backend, "app")
    assert result.returncode == 3, result.stderr


def test_dependency_served_only_as_sdist_finds_no_resolution(
    world: World, backend: str, out: Path
) -> None:
    sdist = world.directory / "dep_b-2.0.tar.gz"
    sdist.write_bytes(b"not built: wheels only")
    world.index.projects["dep-b"] = [ServedFile(sdist, metadata=False)]
    publish(world)
    result = uvxl(world, backend, "app")
    assert result.returncode == 1
    assert "[C5" in result.stderr


@pytest.mark.parametrize(
    ("requirement", "message"),
    [
        ("app[", "invalid requirement"),
        ("app @ https://x.invalid/app.whl", "only a name, extras and a version"),
        ("app; python_version > '3'", "only a name, extras and a version"),
    ],
)
def test_invalid_from_is_an_error(
    world: World, backend: str, out: Path, requirement: str, message: str
) -> None:
    publish(world)
    result = uvxl(world, backend, "--from", requirement, "app")
    assert result.returncode == 1
    assert message in result.stderr
    assert not out.exists()
