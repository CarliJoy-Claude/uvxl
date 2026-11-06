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
- **M5.** No `Pylock` URL on the root wheel while a locked install was requested: fail.

### 3.2 File name

- **N1.** The last path segment of the URL MUST match the PEP 751 regex `^pylock\.([^.]+\.)?toml$`.
  Nothing else is checked: providing the correct lock is the maintainer's job, not the installer's to guess.
  (Note: the regex forbids dots in the name part, so `pylock.tool-1.2.3.toml` is invalid.)

### 3.3 Fetching

- **F1.** Opt-in only (flag or setting). Default installer behavior is unchanged.
- **F2.** Only the lock of the explicitly requested root package is considered. Locks of dependencies are never fetched.
- **F3.** Credentials:
  - If the lock URL is covered by a configured index, that index's credentials are used. This is what enables private lock hosting.
  - Coverage: same origin, and the lock URL's path lies under the index's base path.
    The base path is the index URL's path without a trailing `simple` or `+simple` segment.
    Example: devpi's `https://pypi.local/user/index/+simple/` covers `https://pypi.local/user/index/+f/<hash>/pylock.toml`, but not `https://pypi.local/user/other/…`.
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
- Names are provisional: distribution `uvlx-py`, import package `uvlx`, console scripts `uvlx` and `pylock-verify`.
  The Rust wheel must not clash: distribution `uvlx-rs`, binary name to decide (for example `uvlx-rs` until one wins).
- Dependencies are declared **only** in each package's `pyproject.toml`. No PEP 723 headers in package code (the earlier single-file script declared them twice; that was a mistake).
- Rust PoC: maturin with `bindings = "bin"` turns the binary into a wheel, so `uv tool install` and `uvx` can use it. The uv crates are on crates.io (0.0.98 matched uv 0.13.6) but are "internal component crates" with no stability promise: pin one exact version family. uv pins Rust 1.99 in its `rust-toolchain.toml`.

## 5. Python PoC: current state and plan

### 5.1 Existing code (migrated 2026-11-05, `uvlx` added 2026-11-05, simplified 2026-11-06)

- The former single-file `verify_and_clean.py` (v2) now lives in `packages/uvlx-py/src/uvlx/`, split into modules:
  `errors`, `toml_values` (typed TOML access, hash tables), `urls`, `uv` (CLI runner, target interpreter), `lock` (N1, C1 to C4), `candidates` (uv's candidate pylock, yanked warnings), `clean` (C5 to C7, render).
- Added for `uvlx`: `root` (uv picks the root, `RootWheel` from METADATA, F7), `pylock_url` (M1 to M5, N1), `fetch` (F4 to F6, no credentials), `environments` (cache, C8, P1), `run` (the flow), `cli` (click: `pylock-verify` = `uvlx.cli:verify_main`, `uvlx` = `uvlx.cli:uvlx_main`).
- CLIs use **click**, not argparse. Tests drive them with `click.testing.CliRunner`.
- Tests in `packages/uvlx-py/tests/`: `helpers.py` (fake index, wheel builder, `World`), `conftest.py` (`world` fixture), `test_clean.py`, `test_cli.py`, `test_pylock_url.py`, `test_uvlx.py`. All passing with uv 0.13.7.
- An older v1 existed that implemented its own PEP 691 HTTP client. It was replaced; do not revive it.
- `packages/uvlx-rs` is a placeholder binary (exits 2) that already builds as a wheel with maturin.

### 5.2 How v2 works (uv CLI as engine)

uv has no Python API (the PyPI `uv` package only ships the binary), so the PoC drives the CLI:

1. Python parses the lock with `tomllib` and checks N1, C1 to C4.
2. `uv pip compile - --constraints <tmp>/constraints.txt --python <target> --format pylock.toml --only-binary :all: --no-header`: the root requirement (`app[extras]==version`) on stdin, the lock's pins (`name==version` lines) in a temp file. Constraints never add packages, which gives C5 and C6 for free.
3. Python parses uv's candidate pylock and checks it against the lock: every package must be locked with the same version (C5.1, C5.2), the lock's `index` must be a configured index (C5.3), only wheels whose hash the lock lists are kept (C5.4, C5.5), and the root keeps the index's wheels of its version (C7).
4. A second `uv pip compile --no-deps --emit-index-url --format requirements.txt` lists the configured indexes (that flag only works in requirements format).
5. The cleaned lock is written with `tomli_w` and installed with `uv pip install -r` (see 5.3).
6. Markers are evaluated for the **target** interpreter (found with `uv python find`, queried with our own `packaging.markers.default_environment()` on its path), not the one running the tool.

`pylock-verify LOCK PACKAGE` (for maintainers, before publishing a lock) is a static check of the file only: N1, and every entry is a registry wheel/sdist entry with hashes, version and index (C3), except one `directory` entry for PACKAGE itself (C2). No uv, no network; everything else is checked at install.
Exit codes: 0 ok, 1 error, 2 usage error.

### 5.3 `uvlx` (run a package with its linked lock, like `uvx`): done

`uvlx [--from REQ] [--python X] [--uv-arg ARG] [--cache-dir DIR] COMMAND [ARGS]...`
Options after COMMAND belong to the command; indexes come from uv's own settings, `UV_*` variables or `--uv-arg=--index-url=URL`. Errors exit 1; once the command runs, its own exit code.

1. **Root (F7):** uv chooses: `uv pip install --no-deps --only-binary :all: --target <tmp> REQ` installs the wheel uv prefers for the target interpreter, and its METADATA holds the Pylock URL.
   From then on the root is bound by name and version: C7 puts every wheel the index serves for that version (URL and hash from the index) into the cleaned lock.
   The root entry has no `index` (PEP 751: SHOULD only); uv then uses the file URL minus the file name, which only affects its cache key.
   Trade-off: if wheels of one version had different METADATA, the Pylock URL could come from another wheel than the one installed later. uv makes the same choice both times, so this is accepted for the PoC.
2. **URL:** `Project-URL` headers read with the email parser (duplicates kept for M2), labels normalized, https only, N1 on the last path segment, fragment hashes for pinned mode, unknown parameters fail (no override flag in the PoC).
   No `Pylock` URL fails with `[M5]`.
3. **Fetch:** httpx2 with `follow_redirects` (limit 5), a request hook refusing non-https hops, streamed body limited to 1 MiB, 30 s timeout. Pinned hash checked before parsing.
   No credentials are sent (F3 is left to the Rust version; for it: httpx2 would drop `Authorization` on cross-origin redirects and never reads netrc by itself).
4. **Verify and clean:** `clean_root(root, lock_name, lock_bytes, uv, extras)`.
5. **Environment:** `<cache>/envs/<sha256(interpreter + cleaned lock)[:32]>` (cache: `--cache-dir`, `$XDG_CACHE_HOME/uvlx` or `~/.cache/uvlx`), built with `uv venv --clear` and `uv pip install -r pylock.uvlx.toml`. The cleaned lock stays in the environment.
   The receipt `uvlx-receipt.json` (P1: lock URL, mode, lock sha256, fetch time, root name and version) is written last; without one the environment is created again.
   The lock is fetched and verified on every run (it is mutable); only the environment is reused.
6. **Run:** `<env>/bin/<command>` with a subprocess; the default command is the requirement's name as typed.

Test-only switch: `FetchPolicy(allow_http=True)` passed as click `obj`. There is no command line flag for it.

Open:
- `uvlx install` (venv plus link in `~/.local/bin`) as a stand-in for `uv tool install --locked`.
- Windows:
  - Virtual environments use `Scripts\python.exe` and `Scripts\<command>.exe` instead of `bin/` (`Uv.create_venv`, `environments.py`).

### 5.5 Rust spike `uvlx-rs` (2026-11-06): works end to end

`packages/uvlx-rs` is a wrapper on uv's crates (`=0.0.99` = uv 0.13.7), about 1,200 lines, of which about 300 are copied glue marked `COPIED from <file> (uv 0.13.7)` / `END COPIED` (changes marked `CHANGED`).

- `main.rs`: parses `["uv", "tool", "uvx", args...]` with `uv_cli::Cli` (uv's own `uvx` does the same), so all `uvx` options work. Runtime setup copied from `crates/uv/src/lib.rs`.
- `glue.rs`: settings preamble (config files, `GlobalSettings`, `CacheSettings`, `ToolRunSettings::resolve`, client builder) and `resolve()`, copied from `resolve_environment` with a `DependencyMode` parameter: `Direct` picks the root like `uv pip install --no-deps`, `Transitive` resolves with the lock's pins as constraints.
- `pylock_url.rs` (M1 to M5, N1) and `lock.rs` (N1, C1 to C4): ports of the Python modules; markers via `MarkerTree::evaluate_pep751`. uv's `PylockToml` keeps markers, sources and hashes private, so the lock has its own serde struct.
- `locked.rs`: the flow. Root METADATA from the PEP 658 file through `RegistryClient::uncached_client` (index auth); no PEP 658: not supported yet. Lock fetch: covered URL (F3 rule) through the same client, otherwise a client with `AuthIntegration::NoAuthMiddleware`. The resolver output is checked (C5.1 to C5.4, C7, yanked from `File.yanked`) and rebuilt as a `Resolution` (`Node::Dist` with the verified hashes, only the wheel uv picked), then `CachedEnvironment::from_locked_resolution` installs and caches it (the pattern of `uv-project-commands/src/check.rs`), and the script runs with `run_to_completion`.
- Test switch: `UVLX_TEST_ALLOW_HTTP=1` allows an http lock URL (M3).
- Not done: P1 receipt, root without PEP 658 metadata (fallback: download the wheel), `--with`, Windows checks.
- Out of scope: uv's client follows https to http redirects (F4); treat as an upstream issue.

### 5.4 Testing approach

- Tests run **real `uv`** against a local fake PEP 691 index (`http.server` on 127.0.0.1) serving JSON pages, PEP 658 `.metadata`, multiple wheels per project, a `yanked` flag, a `replacement` file (valid wheel with different bytes, for hash-tamper tests) and optional Basic auth.
- Built test wheels need METADATA, WHEEL and RECORD, or uv refuses them.
- Tamper tests must serve a *valid* wheel with different bytes; a broken zip fails before the hash check.
- Fixture environment: `UV_NO_CACHE=1`, temp `HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME` and `XDG_CACHE_HOME`, no `NETRC`, `chdir` to tmp, proxies removed, `no_proxy=127.0.0.1`.
- Tests that need `uv` are skipped when it is not on PATH; `test_uvlx_rs.py` runs the Rust binary against the same fake index and is skipped when it is not built (`$CARGO_TARGET_DIR/debug/uvlx-rs`).
- Pass `--allow-insecure-http`-style overrides only in tests; production code requires https.
- The fake index also serves `extra_files` (a hosted lock) and `redirects`; a second `FakeIndex` on another port is a foreign origin. Test wheels can carry `Project-URL`s and a console script that writes its arguments to `$UVLX_TEST_OUT` and exits 3.

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
- Without `index` on a pylock entry, uv uses the file URL minus the file name as the index; per its own comment the only effect is the cache key (`crates/uv-lock/src/lock/export/pylock_toml.rs`, commit 24688aa).
- `PylockToml::to_resolution` (`crates/uv-lock/src/lock/export/pylock_toml.rs`) takes URLs and sources from the lock as they are, including vcs, directory and archive. Verify-and-clean must run before it.
- The resolver's `ResolutionMetadata` (`crates/uv-pypi-types/src/metadata/metadata_resolver.rs`) has no Project-URLs and is rkyv-cached (17 construction sites). Reading the `Pylock` URL in Rust should fetch the raw METADATA separately rather than extend that struct.
- uv's auth middleware applies index credentials by URL prefix, so a lock under an index path would get its credentials automatically in a Rust implementation.
- `uv_tool_commands::run` takes 27 parameters and resolves internally with no hook for a pre-built resolution; a Rust `uvlx` needs its own run flow around `sync_environment`, plus copied settings glue from `crates/uv/src/lib.rs`.
- Earlier experiments (uv 0.13.6, httpie): `uv run --frozen` with a hand-edited `uv.lock` pointing the root at the uploaded wheel works, but plain `uv run` re-locks it; pylock is ignored by `uvx`.
- uv 0.13.7 crates: `ResolutionMetadata` has no Project-URLs and the resolver caches only that struct; `Metadata23::project_urls` is a map (duplicate labels lost). Read METADATA via the PEP 658 file instead.
- uv 0.13.7 crates: the auth middleware tries realm-cached credentials and netrc for any URL after a 401; `AuthIntegration::NoAuthMiddleware` turns it off. Cross-origin redirects drop credentials (`CrossOriginCredentialsPolicy::Secure`), but https to http redirects are followed.
- uv 0.13.7 crates: `uv pip install --no-deps` is a resolution with `DependencyMode::Direct` (`uv-cli/src/settings.rs`, `uv-pip-commands/src/install.rs`).
- uv 0.13.7 crates: `CachedEnvironment::from_locked_resolution` takes a ready `Resolution`, verifies its hashes and caches the environment by it; `Resolution::new`, `Node` and `Edge` are public.
- uv 0.13.7: `--emit-index-url` prints index URLs **with** their user info.
- uv 0.13.7: `uv auth token URL` only sees `uv auth login` credentials, by URL prefix, and only with `--username` when they were stored with a user name. It does not see credentials from index URLs, `UV_INDEX_*` variables or netrc.
- uv 0.13.7: `uv pip install --target DIR --no-deps --require-hashes -r -` works with `--python`; the dist-info keeps the wheel's `WHEEL` file, whose `Tag` lines identify the installed wheel.
- uv 0.13.5: `uv pip install -r pylock.toml` checks the locked `size` before the hash; a tampered file of another size fails with "Size mismatch". Tamper tests need a same-size twin to reach the hash check. It also prints a "`--pylock` option is experimental" warning.

## 7. Estimates and options considered

| Option | Effort | Persistent install | Coupling |
|---|---|---|---|
| Fork uv, `uv tool install --locked` | about 1,500 to 2,200 lines Rust, 2 to 3 weeks | yes | rebase conflicts |
| Rust `uvlx` on uv crates (wheel via maturin) | about 1,100 to 1,700 lines | no (run-only) | compile errors on bumps |
| Python `uvlx` driving the uv CLI | about 300 to 500 lines on top of verify-and-clean | no (`uvlx install` workaround) | uv output formats |

Order agreed: Python PoC first to prove the semantics; Rust PoC later, porting the tested Python rules one by one and reusing the same test fixtures where possible.

## 8. Code conventions (strict)

### 8.1 PoC principles

- This is a **proof of concept**, not a program that covers every case. Easy to understand beats complete.
  Prefer the shortest code that shows the rule; leave rare cases out and list them under "Open" in section 5 instead of handling them.
- Deliberately ignored: concurrent runs building the same environment, exit code details (any non-zero exit is fine for errors), cache cleanup.
- **Commit each logical step** separately (refactor, feature, rule change, docs), so the git history shows what was done. Run the checks before each commit.
- Command line interfaces use **click** (not argparse).
- HTTP uses **httpx2** (not urllib).
- **No authentication in the Python PoC:** the lock is fetched without any credentials, so it must be readable without login. F3 (credentials for privately hosted locks) is implemented only in the Rust version, which can use uv's internal credential handling.

### 8.2 Style

- Target **Python 3.12** (`requires-python = ">=3.12"`).
- Format with **`ruff format`**; lint with `ruff check` (rules E, F, I, UP, B, SIM, RUF, PL; `SIM108` and `PLR2004` ignored). Type-check with **`mypy --strict`**. All must pass before a change is done.
- **No ternary operator** (`x if c else y`): use explicit `if`/`else` blocks. Explicit over implicit.
- **Immutables everywhere:** `Mapping`, `Sequence`, `Set` in signatures; tuples and frozensets in code; `@dataclass(frozen=True, slots=True)`.
- **Returned mappings are wrapped in `types.MappingProxyType`** (switch to `frozendict` once the target is Python 3.15+).
- Error messages carry the spec rule ID in brackets.
- Exceptions: `VerifyError` (any error) and `LockRejected` (the lock cannot be used). The rule ID in the message tells the rest. Exit codes are not important in the PoC.
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
