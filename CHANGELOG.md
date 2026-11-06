# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- `uvxl`: runs a Python application like `uvx`, installed with the lock its `Pylock` Project-URL links, verified against the configured indexes.
  Picks the Rust backend (`uvxl[rust]`) if installed, otherwise the Python one; `UVXL_BACKEND` forces one.
- `uvxl-py`: Python backend driving the uv command line, with uvx's option names.
- `uvxl-rs`: Rust backend on uv's crates (0.0.99, uv 0.13.7), with all of uvx's options; sends index credentials to a lock hosted under a configured index.
- `pylock-verify LOCK PACKAGE`: checks that a lock only has entries a locked install accepts.
