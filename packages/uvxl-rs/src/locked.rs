//! The locked `uvx` flow (spec section 3), on top of uv's crates:
//!
//! 1. uv picks the root wheel: a `--no-deps` resolution (F7).
//! 2. The root's METADATA (PEP 658 file) holds the Pylock URL (M1 to M5, N1).
//! 3. The lock is fetched, with index credentials only for a covered URL (F3, F5, F6).
//! 4. uv resolves the root with the lock's pins as constraints; every package
//!    it picks must be locked, served by a configured index and hash-match (C5).
//! 5. The checked packages become a `Resolution`; uv installs it into a cached
//!    environment, verifying the hashes (C8), and runs the command.

use std::collections::HashMap;
use std::ffi::OsString;
use std::str::FromStr;
use std::sync::Arc;

use anyhow::{Context as _, Result, bail};
use petgraph::graph::DiGraph;
use sha2::{Digest, Sha256, Sha384, Sha512};
use uv_client::{AuthIntegration, RegistryClient};
use uv_command_support::ExitStatus;
use uv_command_support::child::run_to_completion;
use uv_configuration::Constraints;
use uv_dispatch::PlatformState;
use uv_distribution_types::{
    BuiltDist, Dist, Edge, NameRequirementSpecification, Node, RegistryBuiltDist,
    RegistryBuiltWheel, Requirement, Resolution, ResolvedDist,
};
use uv_environment_operations::environment::CachedEnvironment;
use uv_install_operations::loggers::SummaryInstallLogger;
use uv_normalize::{ExtraName, PackageName};
use uv_pypi_types::{HashAlgorithm, HashDigest, VerbatimParsedUrl};
use uv_python_discovery::{PythonDownloadReporter, PythonInstallation};
use uv_python_interpreter::PythonEnvironment;
use uv_python_types::{EnvironmentPreference, PythonRequest};
use uv_redacted::SafeUrl;
use uv_requirements::RequirementsSpecification;
use uv_resolver::{DependencyMode, ResolverOutput};

use crate::glue::{Context, registry_client, resolve};
use crate::lock::{LockEntry, common_verified, parse_lock};
use crate::pylock_url::{LockUrl, find_lock_url};

const MAX_LOCK_SIZE: usize = 1024 * 1024;

pub(crate) async fn run(context: Context<'_>) -> Result<ExitStatus> {
    let args = &context.args;
    for (option, used) in [
        (
            "--with",
            !args.with.is_empty() || !args.with_requirements.is_empty(),
        ),
        ("--with-editable", !args.with_editable.is_empty()),
        ("--constraints", !args.constraints.is_empty()),
        ("--overrides", !args.overrides.is_empty()),
    ] {
        if used {
            bail!("{option} is not supported with a locked run");
        }
    }
    let Some((command, command_args)) = args.command.as_deref().and_then(<[_]>::split_first) else {
        bail!("no command given");
    };
    let command = command.to_string_lossy().into_owned();
    let from = args.from.clone().unwrap_or_else(|| command.clone());
    let requested = uv_pep508::Requirement::<VerbatimParsedUrl>::from_str(&from)
        .with_context(|| format!("invalid requirement {from:?}"))?;
    let executable = if args.from.is_some() {
        command.clone()
    } else {
        // Like uvx: the command is the package name as typed.
        from.split(|c: char| "[<>=!~;@ ".contains(c))
            .next()
            .unwrap_or(&from)
            .to_owned()
    };

    // COPIED from crates/uv-tool-commands/src/run.rs `get_or_create_environment`
    // (uv 0.13.7): discover an interpreter. CHANGED: no tool-name Python requests.
    let reporter = PythonDownloadReporter::single(context.printer);
    let python_request = args.python.as_deref().map(PythonRequest::parse);
    let interpreter = PythonInstallation::find_or_download(
        python_request.as_ref(),
        EnvironmentPreference::OnlySystem,
        context.globals.python_preference,
        context.globals.python_arch,
        context.globals.python_downloads,
        &context.client_builder,
        &context.cache,
        Some(&reporter),
        args.install_mirrors.mirrors(),
        args.install_mirrors.python_downloads_json_url.as_deref(),
    )
    .await?
    .into_interpreter();
    // END COPIED
    // As in crates/uv-project-commands/src/check.rs before `from_locked_resolution`.
    let interpreter = CachedEnvironment::base_interpreter(&interpreter, &context.cache)?;
    let state = PlatformState::default();
    let settings = &args.settings.resolver;
    let client = registry_client(
        settings,
        &context.client_builder,
        &interpreter,
        &context.cache,
    )?;

    // 1. uv picks the root (F7).
    let root_spec = format!("{}{}", requested.name, version_part(&requested));
    let output = resolve(
        vec![RequirementsSpecification::parse_package(&root_spec)?],
        Vec::new(),
        DependencyMode::Direct,
        &client,
        &interpreter,
        &context,
        &state,
    )
    .await?;
    let root = registry_wheel(output, &requested.name)?;
    let root_version = root.filename.version.clone();

    // 2. The Pylock URL from the root's METADATA.
    let metadata = root_metadata(&client, &root).await?;
    let lock_url = find_lock_url(&metadata)?;

    // 3. Fetch the lock.
    let content = fetch_lock(&client, &context, &lock_url).await?;

    // 4. Resolve within the lock and check every package against it.
    let extras: Vec<ExtraName> = requested.extras.to_vec();
    let lock = parse_lock(
        &lock_url.file_name(),
        &content,
        &requested.name,
        interpreter.markers(),
        &extras,
    )?;
    let constraints = lock
        .iter()
        .map(|(name, entry)| constraint(name, entry))
        .collect::<Result<Vec<_>>>()?;
    let mut pinned_root = format!("{}", requested.name);
    if !extras.is_empty() {
        let names: Vec<String> = extras.iter().map(ToString::to_string).collect();
        pinned_root.push_str(&format!("[{}]", names.join(",")));
    }
    pinned_root.push_str(&format!("=={root_version}"));
    let output = resolve(
        vec![RequirementsSpecification::parse_package(&pinned_root)?],
        constraints,
        DependencyMode::Transitive,
        &client,
        &interpreter,
        &context,
        &state,
    )
    .await
    .context("[C5] uv finds no resolution within the lock's pins")?;
    let resolution = checked_resolution(output, &requested.name, &root_version, &lock, settings)?;

    // 5. Install (hashes verified by uv) and run.
    let environment = CachedEnvironment::from_locked_resolution(
        &resolution,
        Constraints::default(),
        &interpreter,
        &args.settings,
        &context.malware,
        &context.client_builder,
        &state,
        Box::new(SummaryInstallLogger),
        context.globals.installer_metadata,
        &context.globals.concurrency,
        &context.cache,
        context.printer,
        context.globals.preview,
    )
    .await?;
    let environment = PythonEnvironment::from(environment);
    #[expect(clippy::print_stderr)]
    {
        eprintln!(
            "uvxl-rs: {} {root_version}, {} lock {}, environment {}",
            requested.name,
            lock_url.mode(),
            lock_url.url,
            environment.root().display()
        );
    }
    let path = environment
        .scripts()
        .join(format!("{executable}{}", std::env::consts::EXE_SUFFIX));
    if !path.is_file() {
        bail!("{} provides no {executable:?}", requested.name);
    }
    let handle = tokio::process::Command::new(&path)
        .args(command_args.iter().map(OsString::as_os_str))
        .spawn()
        .with_context(|| format!("cannot run {}", path.display()))?;
    run_to_completion(handle).await
}

fn version_part(requirement: &uv_pep508::Requirement<VerbatimParsedUrl>) -> String {
    match &requirement.version_or_url {
        Some(uv_pep508::VersionOrUrl::VersionSpecifier(specifiers)) => specifiers.to_string(),
        _ => String::new(),
    }
}

fn constraint(name: &PackageName, entry: &LockEntry) -> Result<NameRequirementSpecification> {
    let requirement = uv_pep508::Requirement::<VerbatimParsedUrl>::from_str(&format!(
        "{name}=={}",
        entry.version
    ))?;
    Ok(NameRequirementSpecification::from(Requirement::from(
        requirement,
    )))
}

/// The registry wheel uv chose for `name` (wheels only in the PoC).
fn registry_wheel(output: ResolverOutput, name: &PackageName) -> Result<RegistryBuiltWheel> {
    for dist in Resolution::from(output).distributions() {
        if let ResolvedDist::Installable { dist, .. } = dist
            && let Dist::Built(BuiltDist::Registry(registry)) = dist.as_ref()
            && &registry.best_wheel().filename.name == name
        {
            return Ok(registry.best_wheel().clone());
        }
    }
    bail!("uv resolved no registry wheel for {name}")
}

fn content_hash(algorithm: HashAlgorithm, content: &[u8]) -> Option<String> {
    let hex = |bytes: &[u8]| bytes.iter().map(|b| format!("{b:02x}")).collect::<String>();
    match algorithm {
        HashAlgorithm::Sha256 => Some(hex(&Sha256::digest(content))),
        HashAlgorithm::Sha384 => Some(hex(&Sha384::digest(content))),
        HashAlgorithm::Sha512 => Some(hex(&Sha512::digest(content))),
        _ => None,
    }
}

/// All stated hashes we can compute must match, and at least one must.
fn verify(content: &[u8], digests: &[HashDigest], what: &str) -> Result<()> {
    let mut checked = false;
    for digest in digests {
        if let Some(actual) = content_hash(digest.algorithm(), content) {
            if actual != digest.digest() {
                bail!(
                    "{what}: {} is {actual}, expected {}",
                    digest.algorithm(),
                    digest.digest()
                );
            }
            checked = true;
        }
    }
    if !checked {
        bail!("{what}: no usable hash to verify");
    }
    Ok(())
}

/// The root's METADATA, from its PEP 658 file through uv's client (index auth).
/// uv's own fetch parses straight into `ResolutionMetadata`, without Project-URLs.
async fn root_metadata(client: &RegistryClient, wheel: &RegistryBuiltWheel) -> Result<String> {
    let Some(hashes) = &wheel.file.dist_info_metadata else {
        bail!(
            "{}: the index serves no PEP 658 metadata (not supported by this PoC yet)",
            wheel.filename
        );
    };
    let mut url = wheel.file.url.to_url()?;
    let path = format!("{}.metadata", url.path());
    url.set_path(&path);
    let response = client.uncached_client(&url).get(url.clone()).send().await?;
    let bytes = response.error_for_status()?.bytes().await?;
    verify(&bytes, hashes.as_slice(), &format!("{url}"))?;
    Ok(String::from_utf8_lossy(&bytes).into_owned())
}

/// Rule F3: the configured index whose credentials may be sent to `url`.
/// Same origin, and the path under the index path without `simple`/`+simple`.
fn covered(url: &url::Url, settings: &uv_settings::ResolverSettings) -> bool {
    settings
        .index_locations
        .allowed_indexes()
        .iter()
        .any(|index| {
            let Ok(index_url) = url::Url::parse(index.url().without_credentials().as_str()) else {
                return false;
            };
            if index_url.origin() != url.origin() {
                return false;
            }
            let mut base = index_url.path().trim_end_matches('/');
            if let Some((head, leaf)) = base.rsplit_once('/')
                && (leaf == "simple" || leaf == "+simple")
            {
                base = head;
            }
            base.is_empty() || url.path() == base || url.path().starts_with(&format!("{base}/"))
        })
}

/// Fetch the lock (F3 to F6): uv's client with index auth for a covered URL,
/// a client without auth middleware for any other host.
async fn fetch_lock(
    client: &RegistryClient,
    context: &Context<'_>,
    lock: &LockUrl,
) -> Result<String> {
    let url = SafeUrl::from_url(lock.url.clone());
    let response = if covered(&lock.url, &context.args.settings.resolver) {
        client.uncached_client(&url).get(url.clone()).send().await
    } else {
        let anonymous = context
            .client_builder
            .clone()
            .auth_integration(AuthIntegration::NoAuthMiddleware)
            .build()?;
        anonymous.for_host(&url).get(url.clone()).send().await
    }
    .with_context(|| format!("[F6] fetching the lock from {url} failed"))?;
    let bytes = response
        .error_for_status()
        .with_context(|| format!("[F6] fetching the lock from {url} failed"))?
        .bytes()
        .await?;
    if bytes.len() > MAX_LOCK_SIZE {
        bail!("[F4] the lock at {url} is larger than {MAX_LOCK_SIZE} bytes");
    }
    if !lock.pinned.is_empty() {
        verify(&bytes, &lock.pinned, "[F5] the lock")?;
    }
    String::from_utf8(bytes.to_vec()).context("the lock is not UTF-8")
}

/// Rules C5.1 to C5.5 and C7: check what uv resolved against the lock and
/// build the `Resolution` to install, with the verified hashes.
fn checked_resolution(
    output: ResolverOutput,
    root: &PackageName,
    root_version: &uv_pep440::Version,
    lock: &HashMap<PackageName, LockEntry>,
    settings: &uv_settings::ResolverSettings,
) -> Result<Resolution> {
    let configured: Vec<String> = settings
        .index_locations
        .allowed_indexes()
        .iter()
        .map(|index| index_key(index.url().without_credentials().as_str()))
        .collect();
    let mut graph = DiGraph::new();
    let root_node = graph.add_node(Node::Root);
    for dist in Resolution::from(output).distributions() {
        let ResolvedDist::Installable { dist, .. } = dist else {
            bail!("uv proposes an installed distribution: {dist}");
        };
        let Dist::Built(BuiltDist::Registry(registry)) = dist.as_ref() else {
            bail!("[C5.4] uv proposes a non-wheel distribution: {dist}");
        };
        let wheel = registry.best_wheel();
        let (name, version) = (&wheel.filename.name, &wheel.filename.version);
        if wheel
            .file
            .yanked
            .as_ref()
            .is_some_and(|yanked| yanked.is_yanked())
        {
            bail!("[C5.4] {name} {version} is yanked");
        }
        let index_hashes = wheel.file.hashes.as_slice();
        let hashes = if name == root {
            if version != root_version {
                bail!("[C7] uv selected {name} {version}, expected {root_version}");
            }
            if index_hashes.is_empty() {
                bail!("[C7] the index states no hash for {}", wheel.filename);
            }
            index_hashes.to_vec()
        } else {
            let Some(entry) = lock.get(name) else {
                bail!("[C5.1] {name} {version} is needed but not in the lock");
            };
            if version != &entry.version {
                bail!(
                    "[C5.2] uv selected {name} {version}, the lock has {}",
                    entry.version
                );
            }
            if !configured.contains(&index_key(&entry.index)) {
                bail!(
                    "[C5.3] the lock names index {:?} for {name}, which is not a configured index",
                    entry.index
                );
            }
            let Some(verified) = entry
                .wheel_hashes
                .iter()
                .find_map(|locked| common_verified(locked, index_hashes))
            else {
                bail!(
                    "[C5.4] {} served by the index does not match the lock",
                    wheel.filename
                );
            };
            verified
        };
        // C5.5: only the wheel uv will install, with URL and size from the index.
        let dist = Dist::Built(BuiltDist::Registry(RegistryBuiltDist {
            wheels: vec![wheel.clone()],
            best_wheel_index: 0,
            sdist: None,
        }));
        let node = graph.add_node(Node::Dist {
            dist: ResolvedDist::Installable {
                dist: Arc::new(dist),
                version: Some(version.clone()),
            },
            hashes: hashes.into_iter().collect(),
            install: true,
        });
        graph.add_edge(root_node, node, Edge::Prod);
    }
    Ok(Resolution::new(graph))
}

/// Identity of an index URL: no user info, no trailing slash.
fn index_key(url: &str) -> String {
    match url::Url::parse(url) {
        Ok(mut parsed) => {
            let _ = parsed.set_username("");
            let _ = parsed.set_password(None);
            parsed.as_str().trim_end_matches('/').to_owned()
        }
        Err(_) => url.trim_end_matches('/').to_owned(),
    }
}
