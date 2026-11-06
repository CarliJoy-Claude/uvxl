"""The Pylock Project-URL (M1 to M5, N1) and the https-only fetch hook (F4)."""

import httpx2
import pytest

from uvxl_py.errors import LockRejectedError, VerifyError
from uvxl_py.fetch import https_only
from uvxl_py.pylock_url import find_lock_url, normalize_label
from uvxl_py.urls import is_insecure_host, origin

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
    with pytest.raises(LockRejectedError, match=r"\[M5\]"):
        find_lock_url(metadata("Homepage, https://h.example/pylock.toml"))


def test_two_pylock_urls_fail() -> None:
    with pytest.raises(LockRejectedError, match=r"\[M2\]"):
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
    with pytest.raises(LockRejectedError, match=r"\[M3\]"):
        find_lock_url(metadata(f"Pylock, {url}"))


def test_pinned_hash_is_parsed_and_removed_from_the_url() -> None:
    found = find_lock_url(
        metadata(f"Pylock, https://h.example/pylock.toml#sha256={HEX}")
    )
    assert found.url == "https://h.example/pylock.toml"
    assert found.pinned == {"sha256": HEX}
    assert found.mode == "pinned"


@pytest.mark.parametrize("fragment", ["md5=abc", f"sha256={HEX}&egg=app"])
def test_only_hash_parameters_are_allowed(fragment: str) -> None:
    with pytest.raises(LockRejectedError, match=r"\[M4\]"):
        find_lock_url(metadata(f"Pylock, https://h.example/pylock.toml#{fragment}"))


def test_unknown_fragment_parameter_fails() -> None:
    entry = f"Pylock, https://h.example/pylock.toml#sha256={HEX}&sigstore=x"
    with pytest.raises(LockRejectedError, match=r"\[M4\]"):
        find_lock_url(metadata(entry))


@pytest.mark.parametrize(
    "name", ["lock.toml", "pylock.tool-1.2.3.toml", "pylock.toml.bak", ""]
)
def test_file_name_must_match_pep_751(name: str) -> None:
    with pytest.raises(LockRejectedError, match=r"\[N1\]"):
        find_lock_url(metadata(f"Pylock, https://h.example/dir/{name}"))


def test_named_lock_file_and_percent_encoding() -> None:
    found = find_lock_url(metadata("Pylock, https://h.example/a%20b/pylock.prod.toml"))
    assert found.file_name == "pylock.prod.toml"


# --------------------------------------------------------------------------
# F4: every hop must be https
# --------------------------------------------------------------------------


def test_http_hop_is_refused() -> None:
    check = https_only(())
    check(httpx2.Request("GET", "https://h.example/pylock.toml"))
    with pytest.raises(VerifyError, match=r"\[F4\]"):
        check(httpx2.Request("GET", "http://h.example/pylock.toml"))


def test_http_hop_to_an_insecure_host_is_allowed() -> None:
    check = https_only(("h.example",))
    check(httpx2.Request("GET", "http://h.example/pylock.toml"))
    with pytest.raises(VerifyError, match=r"\[F4\]"):
        check(httpx2.Request("GET", "http://other.example/pylock.toml"))


# --------------------------------------------------------------------------
# --allow-insecure-host: uv's syntax and matching
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hosts", "allowed"),
    [
        (("*",), True),
        (("h.example",), True),
        (("h.example:8080",), True),
        (("http://h.example:8080",), True),
        (("https://h.example",), False),
        (("h.example:9090",), False),
        (("other.example",), False),
        ((), False),
    ],
)
def test_insecure_host_matching(hosts: tuple[str, ...], allowed: bool) -> None:
    assert is_insecure_host("http://h.example:8080/pylock.toml", hosts) is allowed


def test_http_lock_url_needs_an_insecure_host() -> None:
    entry = metadata("Pylock, http://h.example/pylock.toml")
    assert find_lock_url(entry, ("h.example",)).url == "http://h.example/pylock.toml"
    with pytest.raises(LockRejectedError, match=r"\[M3\]"):
        find_lock_url(entry, ("other.example",))


def test_origin_defaults_the_port_by_scheme() -> None:
    assert origin("http://h.example/x") == ("http", "h.example", 80)
    assert origin("https://h.example/x") == ("https", "h.example", 443)
