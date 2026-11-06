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

Final home: https://github.com/CarliJoy/uvxl (MIT). Work happens on the fork https://github.com/CarliJoy-Claude/uvxl (git remote `fork`).

```
.
├── CLAUDE.md, README.md, CHANGELOG.md, LICENSE
├── pyproject.toml              # virtual workspace root: dev group, ruff, mypy, pytest, coverage
├── uv.lock
├── .pre-commit-config.yaml     # prek hooks in groups lint, test, build, wheels: CI runs the same
├── .github/workflows/          # ci.yml (checks, wheels, wheel tests), release.yml (PyPI)
├── docs/                       # spec.md (section 3), uv-findings.md (section 6)
├── scripts/                    # rust-coverage.sh, test-wheels.sh, set-rust-version.py
├── tests/                      # all tests (one conftest.py and helpers.py)
└── packages/
    ├── uvxl/                   # meta package: `uvxl` and `pylock-verify` commands, picks a backend
    ├── uvxl-py/                # Python backend (import uvxl_py, command uvxl-py)
    └── uvxl-rs/                # Rust backend on uv's crates (binary uvxl-rs), maturin bindings = "bin"
```

- `uvxl` depends on `uvxl-py`; `uvxl[rust]` adds `uvxl-rs`. `uvxl` runs `uvxl-rs` if it is installed in the same environment (scripts directory, not PATH: `uv tool install` links only uvxl's own commands), otherwise `uvxl-py` in-process. `UVXL_BACKEND=rust|python` forces one; an unknown value or a missing backend is an error.
- Packaging: `uvxl` and `uvxl-py` build with hatchling and hatch-vcs (version from the git tag, `raw-options.root = "../.."`); `uvxl-rs` with maturin, version from `Cargo.toml`, set from the tag by `scripts/set-rust-version.py` in releases. Each package has a copy of `LICENSE`.
- Dependencies are declared **only** in each package's `pyproject.toml`. No PEP 723 headers in package code.
- uv crates: pinned `=0.0.99` (uv 0.13.7), "internal component crates" without stability promise; Rust 1.99.
- The project environment may live elsewhere (`UV_PROJECT_ENVIRONMENT`, here `/home/dev/venv`); `uv sync` rebuilds `uvxl-rs` when its Rust sources change (uv `cache-keys`).

## 5. Current state

### 5.1 Python backend `uvxl-py`

Modules in `packages/uvxl-py/src/uvxl_py/`: `errors`, `toml_values` (typed TOML access, hash tables), `urls` (origins, index keys, `--allow-insecure-host` matching), `venv` (bin or Scripts via `sysconfig`), `uv` (uv CLI runner, target interpreter, markers), `lock` (N1, C1 to C4, `pylock-verify`), `candidates` (uv's candidate pylock, yanked warnings), `clean` (C5 to C7, render), `root` (uv picks the root, F7), `pylock_url` (M1 to M5, N1), `fetch` (F4 to F6, httpx2), `environments` (cache, C8, P1), `run` (the flow), `cli` (click).

uv has no Python API, so the backend drives the CLI:

1. **Root (F7):** `uv pip install --no-deps --only-binary :all: --target <tmp> REQ`: uv picks the wheel, its METADATA holds the Pylock URL. From then on the root is bound by name and version: the cleaned lock lists every wheel the index serves for that version, without `index` (uv then uses the file URL minus the file name, which only affects its cache key). Trade-off: wheels of one version with different METADATA; accepted for the PoC.
2. **URL:** Project-URL headers via the email parser (duplicates kept for M2), labels normalized, https only (http only for `--allow-insecure-host` hosts), N1, fragment hashes for pinned mode, any other fragment parameter fails (M4).
3. **Fetch:** httpx2, redirects (limit 5), a request hook refusing non-https hops except to insecure hosts, 1 MiB, 30 s, pinned hash before parsing. **No credentials** (F3 is Rust only).
4. **Verify and clean:** the lock's pins as constraints for `uv pip compile --only-binary :all: --format pylock.toml` (constraints never add packages: C5, C6), then C5.1, C5.3, C5.4 (hashes, yanked from uv's stderr warning), C5.5, C7 against uv's candidate pylock. C5.2 and the root version hold by construction (`==` pins). Configured indexes come from `uv pip compile --emit-index-url`. Markers are evaluated for the **target** interpreter with our own `packaging` on its path.
5. **Environment:** `<uv cache dir>/uvxl/envs/<sha256(interpreter + cleaned lock)[:32]>` via `uv venv --clear` and `uv pip install -r pylock.uvxl.toml`; receipt `uvxl-receipt.json` (P1) written last. The lock is fetched and verified on every run.
6. **Run:** the console script with a subprocess, its exit code passed on.

CLI: uvx's option names. `--from`, `-p/--python`; resolver options (indexes, `--index-strategy`, `--keyring-provider`, `--resolution`, `--prerelease`, `--exclude-newer`) go to the `uv pip` calls, global options (cache, offline, insecure hosts, config, Python discovery) to every uv call, `-q`/`-v` only to calls whose output is not read. `--with`, `--with-editable`, `--with-requirements`, `--constraints`, `--overrides` are refused. Errors exit 1, usage errors 2.

`pylock-verify LOCK PACKAGE` (provided by `uvxl`, code in `uvxl_py`): static check of a lock before publishing: N1, every entry a registry wheel/sdist with hashes, version and index (C3), except one `directory` entry for PACKAGE itself (C2).

### 5.2 Rust backend `uvxl-rs`

A wrapper on uv's crates, about 1,200 lines, about 300 of them copied glue marked `COPIED from <file> (uv 0.13.7)` / `END COPIED` (changes `CHANGED`).

- `main.rs`: parses `["uv", "tool", "uvx", args...]` with `uv_cli::Cli`, so all uvx options work; runtime setup copied from `crates/uv/src/lib.rs`.
- `glue.rs`: settings preamble, registry client, `resolve()` copied from `resolve_environment` with a `DependencyMode` (`Direct` for the root like `uv pip install --no-deps`, `Transitive` with the lock's pins as constraints), wheels only (`NoBuild::All`); `registry_dists` adapts uv's resolution types.
- `pylock_url.rs` (M1 to M5, N1) and `lock.rs` (C1 to C4): ports of the Python rules; markers via `MarkerTree::evaluate_pep751`; the lock has its own serde struct (uv's `PylockToml` keeps markers, sources and hashes private).
- `locked.rs`: the flow. Root METADATA from the PEP 658 file through `RegistryClient::uncached_client` (index auth; no PEP 658: not supported). Lock fetch: a URL covered by a configured index (F3 rule) through the same client, otherwise a client with `AuthIntegration::NoAuthMiddleware`; a response that is still a redirect is `[F4]`. The resolution is checked (C5.1, C5.3, C5.4, C7, yanked from `File.yanked`) and rebuilt as a `Resolution` with the verified hashes, then `CachedEnvironment::from_locked_resolution` installs and caches it; the script runs with `run_to_completion`.
- Same refusals and `--allow-insecure-host` rule as the Python backend. P1 is skipped (uv caches the environment by the resolution).
- Differences to the Python backend (tested): index credentials for a covered lock (F3), an invalid pinned digest is `[M4]` (Python: `[F5]`), a root whose index states no hash is `[C7]` (Python: uv computes the hash).
- Not done: root without PEP 658 metadata, `-v` logging (uv's tracing setup is private to the `uv` binary crate). Out of scope: uv's client follows https to http redirects (upstream).

### 5.3 Tests and coverage

- All tests in `tests/`, running **real uv** against a fake PEP 691 index on 127.0.0.1 (`helpers.FakeIndex`): JSON pages, PEP 658 metadata (optional, other hash algorithms), several wheels per project, `yanked`, `replacement` bytes for tamper tests, pages without hashes or sizes, failing pages (500), Basic auth, `extra_files` (hosted locks) and `redirects`; a second `FakeIndex` is a foreign origin.
- `test_backends.py`: everything both backends support, run through `python -m uvxl` with `UVXL_BACKEND=python` and `=rust`; assertions only on exit code, the test script's output, rule IDs, index requests and the environment path. `test_uvxl_py.py` and `test_uvxl_rs.py`: behaviour of one backend only. `test_meta.py`: backend selection, also in real throwaway environments without a backend. `test_clean.py`, `test_cli.py`, `test_pylock_url.py`: Python units.
- **Coverage, no mocks:** Python 100% line and branch (`coverage` with `patch = subprocess`); Rust 100% lines for `pylock_url.rs`, `lock.rs`, `locked.rs`, measured from the same pytest run with an instrumented binary (`scripts/rust-coverage.sh`, cargo-llvm-cov; `glue.rs` and `main.rs` reported, not gated). Unreachable defensive branches are removed rather than faked.
- Built test wheels need METADATA, WHEEL and RECORD; tamper tests need a *valid* wheel of the same size to reach the hash check.
- Fixture environment: `UV_NO_CACHE=1` (removed where environments must persist), temp `HOME` and XDG directories, no `NETRC`, `chdir` to tmp, `no_proxy=127.0.0.1`. The fake index is plain http: tests pass `--allow-insecure-host 127.0.0.1`.
- The suite also runs against **installed wheels** (prek group `wheels`, manual stage: `scripts/test-wheels.sh` installs `dist/` into `build/test-wheels-venv` and runs `pytest`; CI `test-wheels` runs the same hook).

### 5.4 CI and releases

- Local and CI parity through prek groups (`.pre-commit-config.yaml`): `lint` (ruff, mypy, cargo fmt and clippy), `test` (cargo test, entry points, pytest with coverage), `build` (sdists and pure wheels into `dist/`), `wheels` (manual stage: `build` plus the uvxl-rs wheel for this platform, then the suite against the wheels).
  A CI job runs a group, or one hook of it; only the cross-platform uvxl-rs wheels use maturin-action instead.
  Jobs without the workspace run prek from a dev-group environment (`uv run --locked --only-group dev prek …`).
- `ci.yml`: `checks` (prek groups `lint` and `test` on Linux), `build-pure` (group `build`), `build-rust` (maturin-action: Linux x86_64/aarch64 manylinux and musllinux, macOS arm64/x86_64, Windows x64), `test-wheels` (hook `test-wheels` on Linux, macOS, Windows × Python 3.12 to 3.14: the built wheels in a fresh environment, the whole suite).
- `release.yml`: on `v*` tags, `ci.yml` with the tag as version, a version check, then PyPI trusted publishing with PEP 740 attestations (environment `pypi`; trusted publishers must be configured on PyPI for `uvxl`, `uvxl-py`, `uvxl-rs`).
- The fork's token can push and read runs and logs, not cancel or re-run them.

### 5.5 Open

- `uvxl install` (persistent locked install) as a stand-in for `uv tool install --locked`.
- Rust: root without PEP 658 metadata; `-v` logging.

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
- `uv_tool_commands::run` takes 27 parameters and resolves internally with no hook for a pre-built resolution; a Rust `uvxl` needs its own run flow around `sync_environment`, plus copied settings glue from `crates/uv/src/lib.rs`.
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
| Rust `uvxl` on uv crates (wheel via maturin) | about 1,100 to 1,700 lines | no (run-only) | compile errors on bumps |
| Python `uvxl` driving the uv CLI | about 300 to 500 lines on top of verify-and-clean | no (`uvxl install` workaround) | uv output formats |

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
- Format with **`ruff format`**; lint with `ruff check` using uvrev's rule set (see `pyproject.toml`; `SIM108` ignored because of the no-ternary rule). Type-check with **`mypy --strict`**. Run everything with `uv run prek run --all-files` before a change is done.
- **Coverage 100%** (Python line and branch, Rust lines of the PoC's own files), without mocks. Remove unreachable branches instead of faking them; test real behaviour with the fake index.
- **No ternary operator** (`x if c else y`): use explicit `if`/`else` blocks. Explicit over implicit.
- **Immutables everywhere:** `Mapping`, `Sequence`, `Set` in signatures; tuples and frozensets in code; `@dataclass(frozen=True, slots=True)`.
- **Returned mappings are wrapped in `types.MappingProxyType`** (switch to `frozendict` once the target is Python 3.15+).
- Error messages carry the spec rule ID in brackets.
- Exceptions: `VerifyError` (any error) and `LockRejectedError` (the lock cannot be used). The rule ID in the message tells the rest. Exit codes are not important in the PoC.
- Markdown: start every sentence on a new line. In prose, do not use the en dash `–`; use `:` instead.
- Rust: `cargo fmt`, `cargo clippy --all-targets -- -D warnings`, `cargo test`, same rule IDs in errors. Mark code copied from uv (`COPIED from` / `END COPIED` / `CHANGED`).

## 9. Commands

```
uv sync                                   # whole workspace; builds uvxl-rs with maturin
uv run prek run --all-files               # all checks: ruff, mypy, cargo fmt/clippy/test, build, tests, coverage
uv run prek run --all-files --group lint  # one group (lint, test, build), as CI runs it
uv run prek run --all-files --stage manual --group wheels  # build all wheels, test them installed
uv run pytest                             # tests only (need uv on PATH)
scripts/rust-coverage.sh                  # Rust coverage from the pytest suite (cargo-llvm-cov)
uv build --package uvxl                   # also uvxl-py; uvxl-rs builds a wheel for this platform
python scripts/set-rust-version.py v1.2.3 # set uvxl-rs's version (releases)
```

## 10. References

- PEP 751: https://peps.python.org/pep-0751/
- pylock.toml spec: https://packaging.python.org/en/latest/specifications/pylock-toml/
- Well-known project URLs: https://packaging.python.org/en/latest/specifications/well-known-project-urls/
- PEP 658 (metadata files), PEP 691 (JSON Simple API), PEP 740 (attestations), PEP 503
- uv `exclude-newer`: https://docs.astral.sh/uv/reference/settings/#exclude-newer
- uv source: https://github.com/astral-sh/uv
- Related prior art: a script in the predecessor thread fetches a lock from a URL and runs the command (https://discuss.python.org/t/pre-pep-include-pylock-toml-files-inside-wheels/99385?page=2); sigstore-python ships a hashed requirements file (https://github.com/sigstore/sigstore-python/blob/main/install/requirements.txt)
