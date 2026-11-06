# uvxl

[![CI](https://github.com/CarliJoy/uvxl/actions/workflows/ci.yml/badge.svg)](https://github.com/CarliJoy/uvxl/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/uvxl)](https://pypi.org/project/uvxl/)
[![Python versions](https://img.shields.io/pypi/pyversions/uvxl)](https://pypi.org/project/uvxl/)
[![License](https://img.shields.io/pypi/l/uvxl)](https://github.com/CarliJoy/uvxl/blob/main/LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)

**uvxl** runs a Python application like `uvx`, but installs it with the lock file its release links through a `Pylock` Project-URL, verified against the indexes you configured.
It is a proof of concept for the pre-PEP ["Add ability to install a package with reproducible dependencies"](https://discuss.python.org/t/pre-pep-add-ability-to-install-a-package-with-reproducible-dependencies/99497/).

```
uv tool install 'uvxl[rust]'
uvxl [uvx options] [--from REQ] COMMAND [ARGS]...
```

A maintainer adds the lock to the package metadata, for example in `pyproject.toml`:

```toml
[project.urls]
Pylock = "https://example.org/locks/pylock.toml"   # optionally #sha256=<hex> to pin it
```

`uvxl` then picks the root wheel as `uvx` would, fetches the lock and treats it as an allowlist of versions and file hashes, never as installation instructions:
every package must be locked, served by a configured index and match a locked hash, or nothing is installed.
A lock can only narrow what normal resolution would accept; it can never add packages, sources or indexes.
The rules (with the IDs used in error messages) are in [docs/spec.md](docs/spec.md).

## Packages

| Package | What it is |
|---|---|
| [`uvxl`](packages/uvxl) | The `uvxl` and `pylock-verify` commands; picks a backend |
| [`uvxl-py`](packages/uvxl-py) | Python backend, drives the uv command line (always installed) |
| [`uvxl-rs`](packages/uvxl-rs) | Rust backend, built on uv's crates (`uvxl[rust]`) |

`uvxl` uses `uvxl-rs` if it is installed, otherwise `uvxl-py`.
`UVXL_BACKEND=rust` or `UVXL_BACKEND=python` forces one.

`pylock-verify LOCK PACKAGE` checks a lock before publishing: only registry wheels and sdists with hashes, plus one directory entry for the package itself (as `uv export` writes it).

## Development

```
uv sync                       # all packages; builds uvxl-rs with maturin (needs Rust 1.99)
uv run prek run --all-files   # format, lint, type check, build, tests with coverage
uv run prek run --all-files --group lint                    # one group: lint, test or build
uv run prek run --all-files --stage manual --group wheels   # tests against the built wheels
```

CI runs the same prek groups (`.pre-commit-config.yaml`).
See [CLAUDE.md](CLAUDE.md) for the design notes and conventions.

## License

[MIT](LICENSE)
