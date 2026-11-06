//! The maintainer's lock, checked on its own (N1, C1 to C4).
//! Port of `uvxl/lock.py`. uv's `PylockToml` keeps markers, sources and
//! file hashes private, so the lock is read with its own small struct.

use std::collections::{BTreeMap, HashMap};
use std::str::FromStr;

use anyhow::{Context, Result, bail};
use serde::Deserialize;
use uv_normalize::{ExtraName, PackageName};
use uv_pep440::{Version, VersionSpecifiers};
use uv_pep508::{MarkerEnvironment, MarkerTree};
use uv_pypi_types::{HashAlgorithm, HashDigest};

#[derive(Deserialize)]
#[serde(rename_all = "kebab-case")]
struct LockFile {
    lock_version: String,
    environments: Option<Vec<String>>,
    requires_python: Option<String>,
    #[serde(default)]
    packages: Vec<LockPackage>,
}

#[derive(Deserialize)]
struct LockPackage {
    name: PackageName,
    version: Option<Version>,
    index: Option<String>,
    marker: Option<String>,
    vcs: Option<toml::Value>,
    directory: Option<toml::Value>,
    archive: Option<toml::Value>,
    sdist: Option<LockFileEntry>,
    wheels: Option<Vec<LockFileEntry>>,
}

#[derive(Deserialize)]
struct LockFileEntry {
    #[serde(default)]
    hashes: BTreeMap<String, String>,
}

/// One applicable lock entry: the allowlist for one package.
pub(crate) struct LockEntry {
    pub(crate) version: Version,
    pub(crate) index: String,
    /// Usable (sha256/384/512) hashes, one list per locked wheel.
    pub(crate) wheel_hashes: Vec<Vec<HashDigest>>,
}

fn usable_hashes(hashes: &BTreeMap<String, String>) -> Vec<HashDigest> {
    hashes
        .iter()
        .filter_map(|(algorithm, digest)| {
            let algorithm = match algorithm.to_ascii_lowercase().as_str() {
                "sha256" => HashAlgorithm::Sha256,
                "sha384" => HashAlgorithm::Sha384,
                "sha512" => HashAlgorithm::Sha512,
                _ => return None,
            };
            HashDigest::new(algorithm, digest.to_ascii_lowercase()).ok()
        })
        .collect()
}

/// Parse and check a lock; return the applicable entry per package name.
pub(crate) fn parse_lock(
    file_name: &str,
    content: &str,
    root: &PackageName,
    markers: &MarkerEnvironment,
    extras: &[ExtraName],
) -> Result<HashMap<PackageName, LockEntry>> {
    let lock: LockFile = match toml::from_str(content) {
        Ok(lock) => lock,
        Err(err) => bail!("{file_name}: invalid TOML: {err}"),
    };

    // C1: supported version, and the lock covers this environment.
    if lock.lock_version != "1.0" {
        bail!("[C1] unsupported lock-version {:?}", lock.lock_version);
    }
    if let Some(environments) = &lock.environments {
        let mut covered = false;
        for marker in environments {
            let marker = MarkerTree::from_str(marker).context("[C1] invalid environments")?;
            covered |= marker.evaluate(markers, &[]);
        }
        if !covered {
            bail!("[C1] the lock does not cover this environment");
        }
    }
    if let Some(requires_python) = &lock.requires_python {
        let specifiers =
            VersionSpecifiers::from_str(requires_python).context("[C1] invalid requires-python")?;
        if !specifiers.contains(&markers.python_full_version().version) {
            bail!("[C1] requires-python excludes this Python");
        }
    }

    let mut selected: HashMap<PackageName, Vec<LockEntry>> = HashMap::new();
    for (position, package) in lock.packages.into_iter().enumerate() {
        let name = package.name.clone();
        let place = format!("packages[{position}] ({name})");
        // C2: the root entry is ignored, whatever its source.
        if &name == root {
            continue;
        }
        // C3: registry sources with hashes only, version and index required.
        for (kind, present) in [
            ("vcs", package.vcs.is_some()),
            ("directory", package.directory.is_some()),
            ("archive", package.archive.is_some()),
        ] {
            if present {
                bail!("[C3] {place} uses a {kind:?} source");
            }
        }
        let wheels = package.wheels.unwrap_or_default();
        if wheels.is_empty() && package.sdist.is_none() {
            bail!("[C3] {place} lists no wheel and no sdist");
        }
        let files = wheels.iter().chain(package.sdist.iter());
        if files
            .into_iter()
            .any(|file| usable_hashes(&file.hashes).is_empty())
        {
            bail!("[C3] {place} has a file without a usable sha256/sha384/sha512 hash");
        }
        let (Some(version), Some(index)) = (package.version, package.index) else {
            bail!("[C3] {place} lacks version or index");
        };
        // C4: entries for other environments are dropped.
        if let Some(marker) = &package.marker {
            let marker =
                MarkerTree::from_str(marker).with_context(|| format!("{place}: invalid marker"))?;
            if !marker.evaluate_pep751(markers, extras, &[]) {
                continue;
            }
        }
        let wheel_hashes = wheels.iter().map(|w| usable_hashes(&w.hashes)).collect();
        selected.entry(name).or_default().push(LockEntry {
            version,
            index,
            wheel_hashes,
        });
    }

    let mut result = HashMap::new();
    for (name, mut entries) in selected {
        if entries.len() > 1 {
            bail!(
                "[C4] {} entries for {name} apply to this environment",
                entries.len()
            );
        }
        result.insert(name, entries.remove(0));
    }
    Ok(result)
}

/// Hashes both sides state, if all of them agree (and there is at least one).
pub(crate) fn common_verified(
    lock: &[HashDigest],
    index: &[HashDigest],
) -> Option<Vec<HashDigest>> {
    let common: Vec<&HashDigest> = index
        .iter()
        .filter(|digest| lock.iter().any(|l| l.algorithm() == digest.algorithm()))
        .collect();
    if common.is_empty() || !common.iter().all(|digest| lock.contains(digest)) {
        return None;
    }
    Some(common.into_iter().cloned().collect())
}
