# uvxl

Run a Python application like `uvx`, but installed with the lock file its `Pylock` Project-URL links, verified against the configured indexes.

```
uvxl [uvx options] [--from REQ] COMMAND [ARGS]...
pylock-verify LOCK PACKAGE
```

`uvxl` picks a backend: `uvxl-rs` (Rust, on uv's crates) if it is installed, otherwise `uvxl-py` (Python, drives the uv command line, always installed).
`UVXL_BACKEND=rust` or `UVXL_BACKEND=python` forces one; it is an error if that one is not installed.

```
uv tool install 'uvxl[rust]'   # with the Rust backend
uv tool install uvxl           # Python backend only
```
