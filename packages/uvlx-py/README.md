# uvlx-py

Python proof of concept for installing a package with the lock file linked from its `Pylock` Project-URL.
It drives the `uv` CLI, which must be on `PATH`.

See `docs/spec.md` in the workspace root for the rules (rule IDs such as `[C5.3]` appear in error messages).

## `pylock-verify-clean`

```
pylock-verify-clean WHEEL LOCK [--extra X] [--python REQ] [--uv PATH] [-o pylock.x.toml] [--install] [-- <args for every uv pip call>]
```

Exit codes: 0 ok, 1 lock rejected, 2 uv, network or usage error.
