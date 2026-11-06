# Pylock Project-URL PoC: specification (draft v3)

Decisions settled 2026-11-01.
Kept in sync with section 3 of `CLAUDE.md`.

Rule IDs are used in code and error messages (for example `[C5.3]`). Keep them stable.

## 3.1 Metadata

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

## 3.2 File name

- **N1.** The last path segment of the URL MUST match the PEP 751 regex `^pylock\.([^.]+\.)?toml$`.
  Nothing else is checked: providing the correct lock is the maintainer's job, not the installer's to guess.
  (Note: the regex forbids dots in the name part, so `pylock.tool-1.2.3.toml` is invalid.)

## 3.3 Fetching

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

## 3.4 Verify and clean

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

## 3.5 Receipt

- **P1.** Record lock URL, fetch time and lock hash in the environment, so a later audit is possible.

## 3.6 Threats and rules

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

## 3.7 Out of scope for the PoC

Signing and attestations (fragment keys reserved), locks for non-root packages, Warehouse support, sdist builds, vulnerability policy.

## 3.8 Notes for a later design document

- **Build policy:** the standard should not force wheels only. That is the installer's choice through existing flags (`--only-binary`, `--no-binary`). Some packages cannot be wheels only (for example the MySQL client on Linux). Locked sdists leave build dependencies unlocked: document it.
- **Index aliases:** strict rejection of unconfigured indexes is right for the PoC. The design should add an explicit alias mapping ("`https://pypi.org/simple` is served by my mirror") for devpi and Artifactory. The key rule stays: never download from an index the installer is not configured to use.
- **Missing files dropped, not an error (C5.4):** write down the reasoning (mirrors, multi-platform locks).
- **Signing (later):** a Sigstore bundle next to the lock, verified against the project's Trusted Publisher identity (PEP 740), keeps updates possible and survives a compromised host. Only for Trusted-Publishing projects. A plain public-key fingerprint in the fragment is a simpler alternative with key management on the maintainer. PEP 751 already has `[[packages.attestation-identities]]`.
- **Index-hosted lock** (like https://github.com/pypi/warehouse/issues/8254) shifts trust to the index but needs index support.
