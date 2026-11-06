"""The Pylock Project-URL (M1 to M5, N1) and the https-only fetch hook (F4)."""

import httpx2
import pytest

from uvlx.errors import FetchError, LockRejected
from uvlx.fetch import FetchPolicy, https_only
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
    with pytest.raises(LockRejected, match=r"\[M5\]"):
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


def test_unknown_fragment_parameter_fails() -> None:
    entry = f"Pylock, https://h.example/pylock.toml#sha256={HEX}&sigstore=x"
    with pytest.raises(LockRejected, match=r"\[M4\]"):
        find_lock_url(metadata(entry))


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
# F4: every hop must be https
# --------------------------------------------------------------------------


def test_http_hop_is_refused() -> None:
    check = https_only(FetchPolicy())
    check(httpx2.Request("GET", "https://h.example/pylock.toml"))
    with pytest.raises(FetchError, match=r"\[F4\]"):
        check(httpx2.Request("GET", "http://h.example/pylock.toml"))
