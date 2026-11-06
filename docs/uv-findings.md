# uv findings

Tested with uv 0.11.32 and 0.13.5; source read at 0.13.6 (commit 564a207).
Kept in sync with section 6 of `CLAUDE.md`, extended as we learn.

- `uv pip compile --format pylock.toml` prints a pylock with the index's wheel URLs, sizes and hashes, and lists **every compatible wheel** of the chosen version.
- uv's pylock output never sets `index`. On install, uv then treats the file URL minus the file name as the index. The cleaned lock therefore sets `index` from the verified lock entry.
- Constraints must be in requirements format: uv refuses a pylock as `-c` ("constraints must be specified in `requirements.txt` format").
- A pylock cannot be installed from stdin (`-r -` parses requirements). The file must be named `pylock.toml` or `pylock.<name>.toml`.
- `--emit-index-url` lists configured indexes from CLI, env vars and config files alike (including named `[[index]]` entries), requirements format only.
- With an exact `==` pin, uv **accepts a yanked version** and only warns on stderr ("`name==version` is yanked"). The PoC parses that warning: fragile, keep a test for it.
- `uv pip install -r pylock.toml` refuses a wheel whose bytes differ from the lock's hash. `--pylock` is not a `uv pip install` flag in 0.11.32 (it printed an "experimental" warning in another path).
- "No solution found" from uv is reported as a generic `[C5]` rejection; a root missing from the index looks the same as a pin conflict.
- `uv tool install` has no locked mode. `--locked` elsewhere in uv means "assert `uv.lock` is up to date", so a new flag with that name would clash in concept.
- uv has a `ToolInstallLocks` preview feature that writes its own lock per tool.
- `PylockToml::to_resolution` (`crates/uv-lock/src/lock/export/pylock_toml.rs`) takes URLs and sources from the lock as they are, including vcs, directory and archive. Verify-and-clean must run before it.
- The resolver's `ResolutionMetadata` (`crates/uv-pypi-types/src/metadata/metadata_resolver.rs`) has no Project-URLs and is rkyv-cached (17 construction sites). Reading the `Pylock` URL in Rust should fetch the raw METADATA separately rather than extend that struct.
- uv's auth middleware applies index credentials by URL prefix, so a lock under an index path would get its credentials automatically in a Rust implementation.
- `uv_tool_commands::run` takes 27 parameters and resolves internally with no hook for a pre-built resolution; a Rust `uvlx` needs its own run flow around `sync_environment`, plus copied settings glue from `crates/uv/src/lib.rs`.
- Earlier experiments (uv 0.13.6, httpie): `uv run --frozen` with a hand-edited `uv.lock` pointing the root at the uploaded wheel works, but plain `uv run` re-locks it; pylock is ignored by `uvx`.
- uv 0.13.5: `uv pip install -r pylock.toml` checks the locked `size` before the hash.
  A tampered file of another size fails with "Size mismatch", never reaching the hash check.
  Tests cover both paths (`test_tampered_download_fails_the_hash_check` uses a same-size twin).
- uv 0.13.5: `uv pip install -r pylock.toml` prints "The `--pylock` option is experimental" on stderr.
- uv 0.13.7: `--emit-index-url` prints index URLs **with** their user info.
- uv 0.13.7: `uv auth token URL` only sees `uv auth login` credentials, by URL prefix, and only with `--username` when they were stored with a user name. It does not see credentials from index URLs, `UV_INDEX_*` variables or netrc.
- uv 0.13.7: `uv pip install --target DIR --no-deps --require-hashes -r -` works with `--python`; the dist-info keeps the wheel's `WHEEL` file, whose `Tag` lines identify the installed wheel.
- uv (source at commit 24688aa): without `index` on a pylock entry, uv uses the file URL minus the file name as the index; per its own comment the only effect is the cache key (`crates/uv-lock/src/lock/export/pylock_toml.rs`).
