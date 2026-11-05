# CLAUDE.md

Proof of concept for installing and running a Python application with **locked dependencies**, where the lock file is linked from the package's own metadata through a `Pylock` Project-URL.
This file holds all context from the design discussion so far.
Read it fully before changing code.

## 1. Background and goal

- PEP 751 (`pylock.toml`) standardizes lock files for reproducing an environment while working on a project.
  Nothing standardizes reproducing the tested environment when *installing* a package for use.
- Packages ship loose dependency ranges on purpose.
  The goal is to keep those, but let a user install one package (an application or tool) with a verified, maintainer-provided dependency set.
- The maintainer started a pre-PEP on discuss.python.org:
  - Current thread: https://discuss.python.org/t/pre-pep-add-ability-to-install-a-package-with-reproducible-dependencies/99497/
  - Predecessor (withdrawn: lock inside the wheel): https://discuss.python.org/t/pre-pep-include-pylock-toml-files-inside-wheels/99385
- Main objections there: wheels are a poor application format, a lock embedded in the thing it describes is odd, and installers should experiment before a standard exists.
  Alternatives with traction: independently hosted locks, a conventional "lock extra", date-based exclusion (`exclude-newer`), and tool-level features in uv and pipx first.
- This PoC follows the "independently hosted lock, linked from metadata" route.
  It needs **no change to the wheel format** and **no index (Warehouse) support**, so consumers can add support on their own.
- Target environment of the author: a restricted company network (energy sector).
  Only internal indexes (devpi) are allowed, pypi.org must never be contacted unless configured, and lock files may be hosted privately on the same index (for example `https://pypi.local/user/index/+f/<hash>/pylock.toml`).
  Separate lock-hosting services are not an option there.

## 2. The idea in one paragraph

A release declares `Project-URL: Pylock, https://…/pylock.toml[#sha256=<hex>]` in its core metadata.
An installer asked to install that package *locked* picks the root wheel first, reads the URL from its metadata, fetches the lock, and then treats the lock as an **allowlist of versions and file hashes**, never as installation instructions.
A verify-and-clean step rebuilds a minimal lock using only what the user's configured indexes actually serve, and the installer then installs that cleaned lock with hash enforcement.
Core property: **a lock can only narrow what normal dependency resolution would accept.
It can never add packages, sources or indexes.**
The lock is mutable on purpose (maintainers can ship security updates of dependencies without a new release), unless the maintainer pins its hash in the URL fragment.

## 3. PoC specification (draft v3, decisions settled 2026-11-01)

Rule IDs are used in code and error messages (for example `[C5.3]`). Keep them stable.

### 3.1 Metadata

- **M1.** The label is `Pylock`, normalized like all Project-URL labels (case and punctuation ignored, see https://packaging.python.org/en/latest/specifications/well-known-project-urls/).
  `Pylock` was chosen because the label decides which parser the installer uses, so it names the format.
- **M2.** More than one `Pylock` entry on a release: fail.
- **M3.** The URL scheme MUST be `https`.
- **M4.** Fragment parameters:
  - `sha256=<hex>` (also `sha384`, `sha512`) pins the exact content of the lock file (**pinned mode**).
  - Without a hash the mode is **mutable**.
  - Any unknown fragment parameter fails, because all fragment parameters are security related.
    An explicit installer flag may override this.
- **No `Pylock` URL on the root wheel while a locked install was requested: fail.**

### 3.2 File name

- **N1.** The last path segment of the URL MUST match the PEP 751 regex `^pylock\.([^.]+\.)?toml$`.
  Nothing else is checked: providing the correct lock is the maintainer's job, not the installer's to guess.
  (Note: the regex forbids dots in the name part, so `pylock.tool-1.2.3.toml` is invalid.)

### 3.3 Fetching

- **F1.** Opt-in only (flag or setting). Default installer behavior is unchanged.
- **F2.** Only the lock of the explicitly requested root package is considered. Locks of dependencies are never fetched.
- **F3.** Credentials:
  - If the lock URL is covered by a configured index (same origin and path prefix, as the installer's normal credential lookup decides), that index's credentials are used. This is what enables private lock hosting.
  - For any other host, no index credentials, netrc entries or keyring tokens are sent.
  - A redirect to a different origin drops all credentials.
- **F4.** HTTPS only, including redirects. Redirect limit, size limit (default 1 MiB), timeout.
- **F5.** In pinned mode the hash is verified before parsing. Mismatch is a hard error.
- **F6.** Any fetch or verification failure is a hard error. No silent fallback to an unlocked install.
- **F7.** The root wheel is selected first, then the lock is fetched. The root version does not change in between.

### 3.4 Verify and clean

Input: lock, root package and chosen version, requested extras, target environment, configured indexes.
Output: a cleaned pylock.

- **C1.** `lock-version` MUST be supported (`1.0`). `environments` and `requires-python` MUST cover the target environment.
- **C2.** Every entry whose normalized name equals the root package is ignored, whatever its source type.
  This accepts the **unmodified `uv export` output**, where the root is a directory entry. That is a deliberate, important property.
  The chosen wheel defines which lock is used, which is enough binding.
- **C3.** Any other entry with `vcs`, `directory` or `archive` rejects the lock.
  Any `wheels` or `sdist` entry without hashes rejects the lock.
  (Implementation: only sha256/sha384/sha512 count as usable hashes. Entries without `version` or `index` are rejected.)
- **C4.** Entries whose `marker` is false in the target environment are dropped.
  If two or more entries remain for one name, the lock is rejected as ambiguous.
- **C5.** Walk and build, starting from the root wheel's `Requires-Dist` (with requested extras and markers):
  1. No lock entry for a needed package: reject (all dependencies must be locked; partial locks are out of scope).
  2. The locked version MUST satisfy the requirement's specifier.
  3. The index named in `packages.index` MUST be a configured index. The installer never contacts or adds an index because of the lock. pypi.org is never used unless configured.
  4. Match the lock's wheel hashes against the files the index serves for that version (common algorithm, equal digest).
     Files not served are dropped, not an error (a mirror may lack other platforms' wheels).
     Yanked files are never used. PoC: sdists are dropped (wheels only).
     No remaining file: reject.
  5. Output entry: name, version, wheels with `url` and `size` from the **index** and hashes from the lock. All other lock fields are discarded.
  6. The dependency graph comes from verified wheel metadata (PEP 658 or the wheel), never from the lock's `dependencies` field.
- **C6.** Lock entries the walk never reaches are never looked up and not part of the output.
- **C7.** The root is added as a registry wheel entry, with URL and hash from the index.
- **C8.** The cleaned lock is installed with hash enforcement (`uv pip install -r pylock.toml`).

### 3.5 Receipt

- **P1.** Record lock URL, fetch time and lock hash in the environment, so a later audit is possible.

### 3.6 Threats and rules

| Threat | Rules |
|---|---|
| Lock host points to packages from elsewhere | C3, C5.3, C5.5 |
| Swapped file bytes | C3, C5.4, C8 |
| Lock injects packages nobody asked for | C5 (graph from verified metadata), C6 |
| Unwanted use of an index (for example pypi.org) | C5.3 |
| A dependency's lock influences others | F2 |
| Credential leak to a foreign lock host | F3 |
| Corrupted or tampered lock (pinned mode) | F5 |
| Silent downgrade of safety | F6, M4 |
| Ambiguous lock | C4 |

Accepted by design: the lock is bound to the release only by the URL; rollback to an older locked set is possible (that is what locking means; no warning for old versions); in mutable mode a compromised host can only choose among versions and files already valid on the configured indexes.
Yanked versions are refused.

### 3.7 Out of scope for the PoC

Signing and attestations (fragment keys reserved), locks for non-root packages, Warehouse support, sdist builds, vulnerability policy.

### 3.8 Notes for a later design document

- **Build policy:** the standard should not force wheels only. That is the installer's choice through existing flags (`--only-binary`, `--no-binary`). Some packages cannot be wheels only (for example the MySQL client on Linux). Locked sdists leave build dependencies unlocked: document it.
- **Index aliases:** strict rejection of unconfigured indexes is right for the PoC. The design should add an explicit alias mapping ("`https://pypi.org/simple` is served by my mirror") for devpi and Artifactory. The key rule stays: never download from an index the installer is not configured to use.
- **Missing files dropped, not an error (C5.4):** write down the reasoning (mirrors, multi-platform locks).
- **Signing (later):** a Sigstore bundle next to the lock, verified against the project's Trusted Publisher identity (PEP 740), keeps updates possible and survives a compromised host. Only for Trusted-Publishing projects. A plain public-key fingerprint in the fragment is a simpler alternative with key management on the maintainer. PEP 751 already has `[[packages.attestation-identities]]`.
- **Index-hosted lock** (like https://github.com/pypi/warehouse/issues/8254) shifts trust to the index but needs index support.

## 4. Repository layout (uv workspace)

```
.
├── CLAUDE.md
├── pyproject.toml              # workspace root, virtual (no [project] build), shared tool config
├── uv.lock
├── docs/
│   ├── spec.md                 # the PoC spec (section 3 of this file, kept in sync)
│   └── uv-findings.md          # section 6 of this file, extended as we learn
└── packages/
    ├── uvlx-py/                # Python PoC (first)
    │   ├── pyproject.toml      # build backend: uv_build (or hatchling)
    │   ├── src/uvlx/
    │   └── tests/
    └── uvlx-rs/                # Rust PoC (later), built as a wheel with maturin (bindings = "bin")
        ├── pyproject.toml
        ├── Cargo.toml
        └── src/
```

- Root `pyproject.toml`: `[tool.uv.workspace] members = ["packages/*"]`, plus `[dependency-groups] dev = [...]` (ruff, mypy, pytest, maturin later) and shared `[tool.ruff]`, `[tool.mypy]`, `[tool.pytest.ini_options]`.
- Names are provisional: distribution `uvlx-py`, import package `uvlx`, console scripts `uvlx` and `pylock-verify-clean`.
  The Rust wheel must not clash: distribution `uvlx-rs`, binary name to decide (for example `uvlx-rs` until one wins).
- Dependencies are declared **only** in each package's `pyproject.toml`. No PEP 723 headers in package code (the earlier single-file script declared them twice; that was a mistake).
- Rust PoC: maturin with `bindings = "bin"` turns the binary into a wheel, so `uv tool install` and `uvx` can use it. The uv crates are on crates.io (0.0.98 matched uv 0.13.6) but are "internal component crates" with no stability promise: pin one exact version family. uv pins Rust 1.99 in its `rust-toolchain.toml`.

## 5. Python PoC: current state and plan

### 5.1 Existing code (migrated 2026-11-05)

- The former single-file `verify_and_clean.py` (v2) now lives in `packages/uvlx-py/src/uvlx/`, split into modules:
  `errors`, `toml_values` (typed TOML access, hash tables), `urls`, `wheel` (root wheel), `uv` (CLI runner, target interpreter), `lock` (N1, C1 to C4), `candidates` (uv's candidate pylock, yanked warnings), `clean` (C5 to C7, render), `cli` (`pylock-verify-clean` = `uvlx.cli:verify_clean_main`).
- Tests in `packages/uvlx-py/tests/`: `helpers.py` (fake index, wheel builder, `World`), `conftest.py` (`world` fixture), `test_clean.py`, `test_cli.py`. 33 tests, all passing with uv 0.13.5.
- An older v1 existed that implemented its own PEP 691 HTTP client. It was replaced; do not revive it, except possibly its credential and redirect handling for fetching the lock (F3, F4).
- `packages/uvlx-rs` is a placeholder binary (exits 2) that already builds as a wheel with maturin.

### 5.2 How v2 works (uv CLI as engine)

uv has no Python API (the PyPI `uv` package only ships the binary), so the PoC drives the CLI:

1. Python parses the lock with `tomllib` and checks N1, C1 to C4.
2. `uv pip compile - --constraints /dev/fd/N --python <target> --format pylock.toml --only-binary :all: --no-header`: the root requirement (`app[extras]==version`) on stdin, the lock's pins (`name==version` lines) through a pipe. Constraints never add packages, which gives C5 and C6 for free.
3. Python parses uv's candidate pylock and checks it against the lock: every package must be locked with the same version (C5.1, C5.2), the lock's `index` must be a configured index (C5.3), only wheels whose hash the lock lists are kept (C5.4, C5.5), and the root must hash-match the given wheel (C7).
4. A second `uv pip compile --no-deps --emit-index-url --format requirements.txt` lists the configured indexes (that flag only works in requirements format).
5. The cleaned lock is written with `tomli_w`; with `--install`, a temp `pylock.toml` is installed with `uv pip install -r`.
6. Markers are evaluated for the **target** interpreter (found with `uv python find`, queried with a small script), not the one running the tool.

CLI today: `verify_and_clean.py WHEEL LOCK [--extra X] [--python REQ] [--uv PATH] [-o pylock.x.toml] [--install] [-- <args passed to every uv pip call>]`.
Exit codes: 0 ok, 1 lock rejected, 2 uv, network or usage error.

### 5.3 Next step: `uvlx` (run a package with its linked lock, like `uvx`)

1. Pick the root: `uv pip compile - --no-deps --only-binary :all: --format pylock.toml` with the requirement on stdin gives URL and hash of the chosen wheel (F7).
2. Read the `Pylock` URL from the root's METADATA (not in the compile output). Idea, untested: `uv pip install --no-deps --target <tmp> app==<version>` and read the dist-info. **Fail if there is no `Pylock` URL.**
3. Fetch the lock: N1, M2 to M4, F3 to F6. Credentials are the open problem: try `uv auth token <url>` (untested whether it covers config, env vars and netrc); fallback netrc and user info in the index URL.
4. Verify and clean. Change the root input from "local wheel file" to "name, version and hash from step 1".
5. Environment cache keyed by the hash of the cleaned lock: `uv venv` and `uv pip install -r pylock.toml`. Reuse on the next run.
6. Run the console script from the environment's `bin/`, uvx-like `--from` handling.
7. Optionally `uvlx install` (venv plus link in `~/.local/bin`) as a stand-in for `uv tool install --locked`.

Also: P1 receipt, the fetch step tests, and Windows (the `/dev/fd` pipe is POSIX only; Windows needs a temp constraints file).

### 5.4 Testing approach

- Tests run **real `uv`** against a local fake PEP 691 index (`http.server` on 127.0.0.1) serving JSON pages, PEP 658 `.metadata`, multiple wheels per project, a `yanked` flag, a `replacement` file (valid wheel with different bytes, for hash-tamper tests) and optional Basic auth.
- Built test wheels need METADATA, WHEEL and RECORD, or uv refuses them.
- Tamper tests must serve a *valid* wheel with different bytes; a broken zip fails before the hash check.
- Fixture environment: `UV_NO_CACHE=1`, temp `HOME` and `XDG_CONFIG_HOME`, `chdir` to tmp, proxies removed, `no_proxy=127.0.0.1`.
- Tests that need `uv` are skipped when it is not on PATH.
- Pass `--allow-insecure-http`-style overrides only in tests; production code requires https.

## 6. uv findings (tested with uv 0.11.32, source read at 0.13.6 / commit 564a207)

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
- uv 0.13.5: `uv pip install -r pylock.toml` checks the locked `size` before the hash; a tampered file of another size fails with "Size mismatch". Tamper tests need a same-size twin to reach the hash check. It also prints a "`--pylock` option is experimental" warning.

## 7. Estimates and options considered

| Option | Effort | Persistent install | Coupling |
|---|---|---|---|
| Fork uv, `uv tool install --locked` | about 1,500 to 2,200 lines Rust, 2 to 3 weeks | yes | rebase conflicts |
| Rust `uvlx` on uv crates (wheel via maturin) | about 1,100 to 1,700 lines | no (run-only) | compile errors on bumps |
| Python `uvlx` driving the uv CLI | about 300 to 500 lines on top of verify-and-clean | no (`uvlx install` workaround) | uv output formats |

Order agreed: Python PoC first to prove the semantics; Rust PoC later, porting the tested Python rules one by one and reusing the same test fixtures where possible.

## 8. Code conventions (strict)

- Target **Python 3.12** (`requires-python = ">=3.12"`).
- Format with **`ruff format`**; lint with `ruff check` (rules E, F, I, UP, B, SIM, RUF, PL; `SIM108` and `PLR2004` ignored). Type-check with **`mypy --strict`**. All must pass before a change is done.
- **No ternary operator** (`x if c else y`): use explicit `if`/`else` blocks. Explicit over implicit.
- **Immutables everywhere:** `Mapping`, `Sequence`, `Set` in signatures; tuples and frozensets in code; `@dataclass(frozen=True, slots=True)`.
- **Returned mappings are wrapped in `types.MappingProxyType`** (switch to `frozendict` once the target is Python 3.15+).
- Error messages carry the spec rule ID in brackets.
- Exceptions: `VerifyError` base, `LockRejected` (exit 1), `UvError` / fetch errors (exit 2), `MetadataError`.
- Markdown: start every sentence on a new line. In prose, do not use the en dash `–`; use `:` instead.
- Rust (later): `cargo fmt`, `cargo clippy -- -D warnings`, same rule IDs in errors.

## 9. Commands

```
uv sync                                   # whole workspace
uv run pytest                             # Python tests (need uv on PATH)
uv run ruff format && uv run ruff check
uv run mypy packages/uvlx-py
uv build --package uvlx-py                # wheel
uv build --package uvlx-rs                # wheel (maturin backend, needs cargo)
```

## 10. References

- PEP 751: https://peps.python.org/pep-0751/
- pylock.toml spec: https://packaging.python.org/en/latest/specifications/pylock-toml/
- Well-known project URLs: https://packaging.python.org/en/latest/specifications/well-known-project-urls/
- PEP 658 (metadata files), PEP 691 (JSON Simple API), PEP 740 (attestations), PEP 503
- uv `exclude-newer`: https://docs.astral.sh/uv/reference/settings/#exclude-newer
- uv source: https://github.com/astral-sh/uv
- Related prior art: a script in the predecessor thread fetches a lock from a URL and runs the command (https://discuss.python.org/t/pre-pep-include-pylock-toml-files-inside-wheels/99385?page=2); sigstore-python ships a hashed requirements file (https://github.com/sigstore/sigstore-python/blob/main/install/requirements.txt)
