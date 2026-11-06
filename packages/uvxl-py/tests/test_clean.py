"""Verify and clean: real uv against a local fake PEP 691 index."""

import base64

import pytest

import uvxl
from helpers import UV, ServedFile, World, build_wheel, sha256_hex

pytestmark = pytest.mark.skipif(UV is None, reason="uv is not installed")


def names(result: uvxl.CleanResult) -> tuple[str, ...]:
    return tuple(package.name for package in result.packages)


def package(result: uvxl.CleanResult, name: str) -> uvxl.CleanPackage:
    (found,) = (p for p in result.packages if p.name == name)
    return found


# --------------------------------------------------------------------------
# Accepted locks
# --------------------------------------------------------------------------


def test_happy_path_with_unmodified_uv_style_lock(world: World) -> None:
    result = world.clean(world.good_entries())
    assert names(result) == ("app", "dep-a", "dep-b")
    assert result.unreached == ("evil",)
    for clean in result.packages:
        if clean.name == "app":
            assert clean.index is None  # the root needs no index
        else:
            assert clean.index == world.index.url
        for wheel in clean.wheels:
            assert wheel.url.startswith(f"http://{world.index.host}/files/")
            expected = sha256_hex(world.wheels[clean.name].read_bytes())
            assert wheel.hashes == {"sha256": expected}


def test_unneeded_entries_are_never_looked_up(world: World) -> None:
    world.clean(world.good_entries())
    assert "/simple/evil/" not in world.requested()
    assert "/simple/dep-c/" not in world.requested()


def test_extras_pull_in_extra_dependencies(world: World) -> None:
    result = world.clean(world.good_entries(), extras=("x",))
    assert names(result) == ("app", "dep-a", "dep-b", "dep-c")


def test_unknown_extra_is_an_error(world: World) -> None:
    with pytest.raises(uvxl.VerifyError, match="no extra"):
        world.clean(world.good_entries(), extras=("nope",))


def test_only_wheels_listed_in_the_lock_are_kept(world: World) -> None:
    second = build_wheel(
        world.directory / "wheels", "dep-b", "2.0", tag="py2.py3-none-any"
    )
    world.index.projects["dep-b"].append(ServedFile(second))
    only_first = world.entry("dep-b", wheels=world.wheel_tables("dep-b")[:1])
    result = world.clean(world.replaced("dep-b", only_first))
    assert [w.filename for w in package(result, "dep-b").wheels] == [
        world.wheels["dep-b"].name
    ]


def test_all_locked_wheels_served_are_kept(world: World) -> None:
    second = build_wheel(
        world.directory / "wheels", "dep-b", "2.0", tag="py2.py3-none-any"
    )
    world.index.projects["dep-b"].append(ServedFile(second))
    result = world.clean(world.good_entries())
    assert sorted(w.filename for w in package(result, "dep-b").wheels) == sorted(
        (world.wheels["dep-b"].name, second.name)
    )


def test_hash_algorithms_the_index_does_not_state_are_ignored(world: World) -> None:
    good = sha256_hex(world.wheels["dep-b"].read_bytes())
    table = {"name": "x.whl", "hashes": {"sha256": good, "sha512": "0" * 128}}
    result = world.clean(world.replaced("dep-b", world.entry("dep-b", wheels=[table])))
    assert "dep-b" in names(result)


def test_entries_with_false_markers_are_dropped_not_rejected(world: World) -> None:
    other = world.entry("dep-a", marker="sys_platform == 'nonexistent'")
    assert names(world.clean((*world.good_entries(), other))) == (
        "app",
        "dep-a",
        "dep-b",
    )


def test_named_lock_file_is_accepted(world: World) -> None:
    path = world.write_lock(world.good_entries(), name="pylock.prod.toml")
    assert "dep-a" in names(world.clean_file(path))


def test_uv_credentials_are_used(world: World) -> None:
    expected = "Basic " + base64.b64encode(b"user:secret").decode()
    world.index.expected_authorization = expected
    authenticated = f"http://user:secret@{world.index.host}/simple"
    result = world.clean(world.good_entries(), index_url=authenticated)
    assert "dep-a" in names(result)
    authorized = [auth for path, auth in world.index.requests if auth == expected]
    assert authorized


# --------------------------------------------------------------------------
# Rejected locks
# --------------------------------------------------------------------------


def test_unconfigured_index_is_rejected(world: World) -> None:
    entry = world.entry("dep-a", index="https://pypi.org/simple")
    with pytest.raises(uvxl.LockRejected, match=r"\[C5\.3\]"):
        world.clean(world.replaced("dep-a", entry))


def test_hash_not_served_by_index_is_rejected(world: World) -> None:
    table = {"name": "x.whl", "hashes": {"sha256": "0" * 64}}
    with pytest.raises(uvxl.LockRejected, match=r"\[C5\.4\]"):
        world.clean(world.replaced("dep-b", world.entry("dep-b", wheels=[table])))


def test_yanked_release_is_refused(world: World) -> None:
    world.index.projects["dep-b"][0].yanked = True
    with pytest.raises(uvxl.LockRejected, match="yanked"):
        world.clean(world.good_entries())


@pytest.mark.parametrize("source", ["vcs", "directory", "archive"])
def test_non_registry_entries_are_rejected(world: World, source: str) -> None:
    extra = {"name": "zzz", source: {"url": "https://evil.invalid/x"}}
    with pytest.raises(uvxl.LockRejected, match=r"\[C3\]"):
        world.clean((*world.good_entries(), extra))


def test_file_without_hashes_is_rejected(world: World) -> None:
    entry = world.entry("dep-b", wheels=[{"name": "x.whl"}])
    with pytest.raises(uvxl.LockRejected, match=r"\[C3\]"):
        world.clean(world.replaced("dep-b", entry))


def test_entry_without_index_is_rejected(world: World) -> None:
    entry = {k: v for k, v in world.entry("dep-b").items() if k != "index"}
    with pytest.raises(uvxl.LockRejected, match=r"\[C3\]"):
        world.clean(world.replaced("dep-b", entry))


def test_two_applicable_entries_for_one_name_are_rejected(world: World) -> None:
    first = world.entry("dep-a", marker="python_full_version >= '3.1'")
    second = world.entry("dep-a", marker="python_full_version >= '3.0'")
    entries = (*world.replaced("dep-a", first), second)
    with pytest.raises(uvxl.LockRejected, match=r"\[C4\]"):
        world.clean(entries)


def test_pins_that_conflict_with_requirements_are_rejected(world: World) -> None:
    with pytest.raises(uvxl.LockRejected, match=r"\[C5\]"):
        world.clean(world.replaced("dep-a", world.entry("dep-a", version="0.5")))


def test_dependency_missing_from_lock_is_rejected(world: World) -> None:
    with pytest.raises(uvxl.LockRejected, match=r"\[C5\.1\]"):
        world.clean(world.replaced("dep-b", None))


def test_root_missing_from_index_is_an_error(world: World) -> None:
    del world.index.projects["app"]
    with pytest.raises(uvxl.VerifyError, match="uv pip install"):
        world.clean(world.good_entries())


@pytest.mark.parametrize("name", ["lock.toml", "pylock.1.0.toml", "pylock.toml.bak"])
def test_lock_file_name_must_match_pep_751(world: World, name: str) -> None:
    path = world.write_lock(world.good_entries(), name=name)
    with pytest.raises(uvxl.LockRejected, match=r"\[N1\]"):
        world.clean_file(path)


def test_unsupported_lock_version_is_rejected(world: World) -> None:
    path = world.write_lock(world.good_entries(), **{"lock-version": "2.0"})
    with pytest.raises(uvxl.LockRejected, match=r"\[C1\]"):
        world.clean_file(path)


def test_lock_that_excludes_this_environment_is_rejected(world: World) -> None:
    path = world.write_lock(
        world.good_entries(), environments=["sys_platform == 'nonexistent'"]
    )
    with pytest.raises(uvxl.LockRejected, match=r"\[C1\]"):
        world.clean_file(path)
