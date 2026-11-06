"""Test support: wheels built on the fly and a fake PEP 691 index."""

import hashlib
import http.server
import json
import shutil
import sys
import threading
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import tomli_w

import uvlx
from uvlx.root import parse_root_request, select_root

UV = shutil.which("uv")

VERSIONS: Mapping[str, str] = {
    "app": "1.0",
    "dep-a": "1.2",
    "dep-b": "2.0",
    "dep-c": "3.0",
    "evil": "9.9",
}


# The console script of test wheels: writes its arguments to $UVLX_TEST_OUT
# and exits with 3, so tests see that arguments and exit codes pass through.
SCRIPT_MODULE = """
import os, sys

def main():
    with open(os.environ["UVLX_TEST_OUT"], "w") as handle:
        handle.write(" ".join(sys.argv[1:]))
    return 3
"""


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_wheel(  # noqa: PLR0913
    directory: Path,
    name: str,
    version: str,
    requires: Sequence[str] = (),
    *,
    extras: Sequence[str] = (),
    tag: str = "py3-none-any",
    salt: str = "",
    project_urls: Sequence[str] = (),
    console_script: bool = False,
) -> Path:
    """A minimal valid wheel. uv refuses wheels without METADATA, WHEEL, RECORD.

    With ``console_script``, the wheel provides a command named like the
    project; see ``SCRIPT_MODULE`` for what it does.
    """
    dist = name.replace("-", "_")
    dist_info = f"{dist}-{version}.dist-info"
    lines = ["Metadata-Version: 2.3", f"Name: {name}", f"Version: {version}"]
    lines.extend(f"Project-URL: {entry}" for entry in project_urls)
    lines.extend(f"Provides-Extra: {extra}" for extra in extras)
    lines.extend(f"Requires-Dist: {requirement}" for requirement in requires)
    wheel_file = (
        f"Wheel-Version: 1.0\nGenerator: test {salt}\n"
        f"Root-Is-Purelib: true\nTag: {tag}\n"
    )
    members: dict[str, str] = {
        f"{dist_info}/METADATA": "\n".join(lines) + "\n\n",
        f"{dist_info}/WHEEL": wheel_file,
    }
    if console_script:
        members[f"{dist}_cli.py"] = SCRIPT_MODULE
        members[f"{dist_info}/entry_points.txt"] = (
            f"[console_scripts]\n{name} = {dist}_cli:main\n"
        )
    record = "".join(f"{member},,\n" for member in (*members, f"{dist_info}/RECORD"))
    path = directory / f"{dist}-{version}-{tag}.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for member, text in members.items():
            archive.writestr(member, text)
        archive.writestr(f"{dist_info}/RECORD", record)
    return path


def wheel_metadata(path: Path) -> bytes:
    with zipfile.ZipFile(path) as archive:
        (name,) = (n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))
        return archive.read(name)


@dataclass
class ServedFile:
    path: Path
    yanked: bool = False
    # Serve these bytes instead, while still advertising the hash of `path`.
    # Must be a valid wheel: a broken zip fails before the hash check.
    replacement: Path | None = None


@dataclass
class FakeIndex:
    """A PEP 691 index on 127.0.0.1 with PEP 658 metadata. Tests mutate it."""

    expected_authorization: str | None = None
    projects: dict[str, list[ServedFile]] = field(default_factory=dict)
    requests: list[tuple[str, str | None]] = field(default_factory=list)
    # Any other path: fixed content (a hosted lock) or a redirect target.
    extra_files: dict[str, bytes] = field(default_factory=dict)
    redirects: dict[str, str] = field(default_factory=dict)

    def start(self) -> None:
        index = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def reply(self, body: bytes, content_type: str) -> None:
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: PLR0911 (one branch per route)
                authorization = self.headers.get("Authorization")
                index.requests.append((self.path, authorization))
                expected = index.expected_authorization
                if expected is not None and authorization != expected:
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", 'Basic realm="test"')
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if self.path in index.redirects:
                    self.send_response(302)
                    self.send_header("Location", index.redirects[self.path])
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if self.path in index.extra_files:
                    self.reply(index.extra_files[self.path], "application/toml")
                    return
                if self.path.startswith("/simple/"):
                    name = self.path.removeprefix("/simple/").strip("/")
                    if name not in index.projects:
                        self.send_error(404)
                        return
                    page = json.dumps(index.page(name)).encode()
                    self.reply(page, "application/vnd.pypi.simple.v1+json")
                    return
                filename = self.path.removeprefix("/files/")
                for served in (f for files in index.projects.values() for f in files):
                    if filename == served.path.name:
                        source = served.path
                        if served.replacement is not None:
                            source = served.replacement
                        self.reply(source.read_bytes(), "application/octet-stream")
                        return
                    if filename == served.path.name + ".metadata":
                        self.reply(wheel_metadata(served.path), "text/plain")
                        return
                self.send_error(404)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        ).start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    @property
    def host(self) -> str:
        return f"127.0.0.1:{self.server.server_address[1]}"

    @property
    def url(self) -> str:
        return f"http://{self.host}/simple"

    def base(self) -> str:
        return f"http://{self.host}"

    def page(self, name: str) -> dict[str, object]:
        files: list[dict[str, object]] = []
        for served in self.projects[name]:
            data = served.path.read_bytes()
            table: dict[str, object] = {
                "filename": served.path.name,
                "url": f"/files/{served.path.name}",
                "hashes": {"sha256": sha256_hex(data)},
                "size": len(data),
                "core-metadata": {"sha256": sha256_hex(wheel_metadata(served.path))},
            }
            if served.yanked:
                table["yanked"] = "test"
            files.append(table)
        return {"meta": {"api-version": "1.1"}, "name": name, "files": files}


@dataclass
class World:
    index: FakeIndex
    directory: Path
    wheels: Mapping[str, Path]

    def uv(self, index_url: str | None = None) -> uvlx.Uv:
        assert UV is not None
        url = index_url
        if url is None:
            url = self.index.url
        return uvlx.Uv(UV, sys.executable, ("--index-url", url))

    def wheel_tables(self, name: str) -> list[dict[str, object]]:
        return [
            {
                "name": served.path.name,
                "url": f"https://attacker.invalid/{served.path.name}",
                "hashes": {"sha256": sha256_hex(served.path.read_bytes())},
            }
            for served in self.index.projects[name]
        ]

    def entry(self, name: str, **overrides: object) -> dict[str, object]:
        """A lock entry. The URLs in the lock are deliberately bogus."""
        table: dict[str, object] = {
            "name": name,
            "version": VERSIONS[name],
            "index": self.index.url,
            "wheels": self.wheel_tables(name),
        }
        return {**table, **overrides}

    def good_entries(self) -> tuple[dict[str, object], ...]:
        """Like an unmodified `uv export`: root as directory, everything pinned."""
        return (
            {"name": "app", "directory": {"path": ".", "editable": True}},
            self.entry("dep-a"),
            self.entry("dep-b"),
            self.entry("dep-c", marker="'x' in extras"),
            self.entry("evil"),
        )

    def replaced(
        self, name: str, entry: Mapping[str, object] | None
    ) -> tuple[Mapping[str, object], ...]:
        """The good entries with one replaced (or removed when entry is None)."""
        result: list[Mapping[str, object]] = []
        for table in self.good_entries():
            if table["name"] != name:
                result.append(table)
            elif entry is not None:
                result.append(entry)
        return tuple(result)

    def write_lock(
        self,
        entries: Sequence[Mapping[str, object]],
        name: str = "pylock.toml",
        **top_level: object,
    ) -> Path:
        document: dict[str, object] = {
            "lock-version": "1.0",
            "created-by": "test",
            **top_level,
            "packages": list(entries),
        }
        path = self.directory / name
        path.write_text(tomli_w.dumps(document), encoding="utf-8")
        return path

    def clean_file(
        self,
        lock: Path,
        extras: Sequence[str] = (),
        index_url: str | None = None,
    ) -> uvlx.CleanResult:
        """Verify and clean ``lock`` for ``app``, chosen by uv from the index."""
        uv = self.uv(index_url)
        root = select_root(uv, parse_root_request("app"))
        return uvlx.clean_root(root, lock.name, lock.read_bytes(), uv, extras)

    def clean(
        self,
        entries: Sequence[Mapping[str, object]],
        extras: Sequence[str] = (),
        index_url: str | None = None,
    ) -> uvlx.CleanResult:
        return self.clean_file(self.write_lock(entries), extras, index_url)

    def requested(self) -> tuple[str, ...]:
        return tuple(path for path, _ in self.index.requests)
