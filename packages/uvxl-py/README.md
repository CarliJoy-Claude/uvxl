# uvxl-py

Python proof of concept for installing a package with the lock file linked from its `Pylock` Project-URL.
It drives the `uv` CLI, which must be on `PATH`.

See `docs/spec.md` in the workspace root for the rules (rule IDs such as `[C5.3]` appear in error messages).

## `pylock-verify`

```
pylock-verify LOCK PACKAGE
```

Checks, before publishing, that LOCK only has entries a locked install accepts: registry wheels and sdists with hashes, version and index, plus one directory entry for PACKAGE itself (as `uv export` writes it).
Exit codes: 0 ok, 1 error, 2 usage error.

## `uvxl-py`

```
uvxl-py [--from REQ] [--python X] [--uv-arg ARG] [--cache-dir DIR] COMMAND [ARGS]...
```

Runs COMMAND like `uvx`, from an environment installed with the lock the root package links through its `Pylock` Project-URL.
The lock is fetched on every run, verified against the configured indexes and cleaned; environments are cached by the cleaned lock and carry a receipt (`uvxl-receipt.json`).
Errors exit 1; once the command runs, its own exit code.
