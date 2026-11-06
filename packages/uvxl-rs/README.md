# uvxl-rs

Rust backend of [uvxl](https://github.com/CarliJoy/uvxl), on top of uv's crates (pinned to `=0.0.99`, which is uv 0.13.7).
Built as a wheel with maturin (`bindings = "bin"`); install it with `uvxl[rust]`, and the `uvxl` command uses it.

```
uvxl-rs [uvx options] [--from REQ] COMMAND [ARGS]...
```

Takes the same options as `uvx` (it parses them with uv's own `uv_cli::Cli`).
The package must declare a `Pylock` Project-URL; the lock is fetched (with the index's credentials only when the lock lies under a configured index), verified against what the configured indexes serve, and installed into a cached environment with hash verification.
`--with`, `--with-editable`, `--with-requirements`, `--constraints` and `--overrides` are refused: they would bypass the lock.
A plain http lock URL is accepted only for a host given with `--allow-insecure-host`, like uv does for indexes.

Code copied from uv is marked `COPIED from <file> (uv 0.13.7)` and `END COPIED`; changes inside copied blocks are marked `CHANGED`.
It is tested with the same Python test suite as the Python backend (`tests/test_backends.py` runs both, `tests/test_uvxl_rs.py` what only this backend does); no Rust knowledge is needed to test it.
`scripts/rust-coverage.sh` measures its coverage from that suite.
