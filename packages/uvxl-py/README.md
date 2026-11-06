# uvxl-py

Python backend of [uvxl](https://github.com/CarliJoy/uvxl): installs and runs a package with the lock file linked from its `Pylock` Project-URL.
It drives the `uv` CLI, which must be on `PATH`.
Usually used through the `uvxl` command (package `uvxl`), which picks this backend if `uvxl-rs` is not installed.

See `docs/spec.md` in the workspace root for the rules (rule IDs such as `[C5.3]` appear in error messages).

## `pylock-verify` (command provided by `uvxl`)

```
pylock-verify LOCK PACKAGE
```

Checks, before publishing, that LOCK only has entries a locked install accepts: registry wheels and sdists with hashes, version and index, plus one directory entry for PACKAGE itself (as `uv export` writes it).
Exit codes: 0 ok, 1 error, 2 usage error.

## `uvxl-py`

```
uvxl-py [uvx options] [--from REQ] COMMAND [ARGS]...
```

Runs COMMAND like `uvx`, from an environment installed with the lock the root package links through its `Pylock` Project-URL.
The lock is fetched on every run, verified against the configured indexes and cleaned; environments are cached in uv's cache directory and carry a receipt (`uvxl-receipt.json`).

Options use uvx's names: `--from`, `-p/--python`, the index options (`--index`, `--default-index`, `-i/--index-url`, `--extra-index-url`, `-f/--find-links`, `--no-index`, `--index-strategy`, `--keyring-provider`), `--resolution`, `--prerelease`, `--exclude-newer`, and the global options `-n/--no-cache`, `--cache-dir`, `--offline`, `--allow-insecure-host`, `--system-certs`, `--config-file`, `--no-config`, `--managed-python`, `--no-managed-python`, `--no-python-downloads`, `-q`, `-v`.
They go to uv unchanged. A plain http lock URL is accepted only for a host given with `--allow-insecure-host` (or `UV_INSECURE_HOST`).
`--with`, `--with-editable`, `--with-requirements`, `--constraints` and `--overrides` are refused: they would bypass the lock.
Errors exit 1; once the command runs, its own exit code.
