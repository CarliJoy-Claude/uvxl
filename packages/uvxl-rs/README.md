# uvxl-rs

Rust implementation of uvxl on top of uv's crates (pinned to `=0.0.99`, which is uv 0.13.7).
Built as a wheel with maturin (`bindings = "bin"`), so `uv tool install` and `uvx` can use it.

```
uvxl-rs [uvx options] [--from REQ] COMMAND [ARGS]...
```

Takes the same options as `uvx` (it parses them with uv's own `uv_cli::Cli`).
The package must declare a `Pylock` Project-URL; the lock is fetched (with the index's credentials only when the lock lies under a configured index), verified against what the configured indexes serve, and installed into a cached environment with hash verification.
`--with`, `--with-editable`, `--with-requirements`, `--constraints` and `--overrides` are refused: they would bypass the lock.
A plain http lock URL is accepted only for a host given with `--allow-insecure-host`, like uv does for indexes.

Code copied from uv is marked `COPIED from <file> (uv 0.13.7)` and `END COPIED`; changes inside copied blocks are marked `CHANGED`.
The end-to-end tests live in `tests/test_uvxl_rs.py` at the workspace root and reuse the Python fake index (`cargo build` first).
