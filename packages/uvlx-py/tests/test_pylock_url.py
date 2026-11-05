"""The Pylock Project-URL (M1 to M4, N1) and credential coverage (F3)."""

import pytest

from uvlx.errors import LockRejected
from uvlx.fetch import covering_index
from uvlx.pylock_url import find_lock_url, normalize_label

HEX = "ab" * 32


def metadata(*project_urls: str) -> bytes:
    lines = ["Metadata-Version: 2.3", "Name: app", "Version: 1.0"]
    lines.extend(f"Project-URL: {entry}" for entry in project_urls)
    return ("\n".join(lines) + "\n\n").encode()


@pytest.mark.parametrize("label", ["Pylock", "pylock", "PYLOCK", "Py-Lock", "py lock"])
def test_label_is_normalized(label: str) -> None:
    assert normalize_label(label) == "pylock"
    found = find_lock_url(metadata(f"{label}, https://h.example/pylock.toml"))
    assert found.url == "https://h.example/pylock.toml"
    assert found.mode == "mutable"


def test_other_labels_are_ignored() -> None:
    with pytest.raises(LockRejected, match=r"\[M1\]"):
        find_lock_url(metadata("Homepage, https://h.example/pylock.toml"))


def test_two_pylock_urls_fail() -> None:
    with pytest.raises(LockRejected, match=r"\[M2\]"):
        find_lock_url(
            metadata(
                "Pylock, https://h.example/pylock.toml",
                "pylock, https://h.example/pylock.other.toml",
            )
        )


@pytest.mark.parametrize(
    "url", ["http://h.example/pylock.toml", "file:///pylock.toml", "pylock.toml"]
)
def test_scheme_must_be_https(url: str) -> None:
    with pytest.raises(LockRejected, match=r"\[M3\]"):
        find_lock_url(metadata(f"Pylock, {url}"))


def test_pinned_hash_is_parsed_and_removed_from_the_url() -> None:
    found = find_lock_url(
        metadata(f"Pylock, https://h.example/pylock.toml#sha256={HEX}")
    )
    assert found.url == "https://h.example/pylock.toml"
    assert found.pinned == {"sha256": HEX}
    assert found.mode == "pinned"


@pytest.mark.parametrize(
    "fragment",
    ["sha256=abc", "sha256=" + "zz" * 32, "sha256", f"sha256={HEX}&sha256={HEX}"],
)
def test_invalid_pins_fail(fragment: str) -> None:
    with pytest.raises(LockRejected, match=r"\[M4\]"):
        find_lock_url(metadata(f"Pylock, https://h.example/pylock.toml#{fragment}"))


def test_unknown_fragment_parameter_fails_unless_allowed() -> None:
    entry = f"Pylock, https://h.example/pylock.toml#sha256={HEX}&sigstore=x"
    with pytest.raises(LockRejected, match=r"\[M4\]"):
        find_lock_url(metadata(entry))
    found = find_lock_url(metadata(entry), allow_unknown_fragment=True)
    assert found.pinned == {"sha256": HEX}


@pytest.mark.parametrize(
    "name", ["lock.toml", "pylock.tool-1.2.3.toml", "pylock.toml.bak", ""]
)
def test_file_name_must_match_pep_751(name: str) -> None:
    with pytest.raises(LockRejected, match=r"\[N1\]"):
        find_lock_url(metadata(f"Pylock, https://h.example/dir/{name}"))


def test_named_lock_file_and_percent_encoding() -> None:
    found = find_lock_url(metadata("Pylock, https://h.example/a%20b/pylock.prod.toml"))
    assert found.file_name == "pylock.prod.toml"


# --------------------------------------------------------------------------
# F3: which configured index covers a lock URL
# --------------------------------------------------------------------------

DEVPI = "https://u:p@pypi.local/user/index/+simple/"


@pytest.mark.parametrize(
    ("url", "covered"),
    [
        ("https://pypi.local/user/index/+f/ab1/pylock.toml", True),
        ("https://pypi.local/user/index/pylock.toml", True),
        ("https://pypi.local/user/other/+f/ab1/pylock.toml", False),
        ("https://pypi.local/user/indexx/pylock.toml", False),
        ("https://pypi.local:8443/user/index/pylock.toml", False),
        ("http://pypi.local/user/index/pylock.toml", False),
        ("https://other.local/user/index/pylock.toml", False),
    ],
)
def test_devpi_index_covers_its_own_paths_only(url: str, covered: bool) -> None:
    assert (covering_index(url, (DEVPI,)) == DEVPI) is covered


def test_plain_simple_index_covers_its_origin() -> None:
    index = "https://pypi.local/simple"
    assert covering_index("https://pypi.local/locks/pylock.toml", (index,)) == index
