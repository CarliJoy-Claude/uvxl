//! Glue copied from uv: settings, cache and client setup, and one resolver
//! call. Each copied block names its source; changes are marked `CHANGED`.

use std::collections::BTreeMap;

use anyhow::{Result, bail};
use uv_cache::{Cache, Refresh};
use uv_cli::settings::{CacheSettings, GlobalSettings, ToolRunSettings};
use uv_cli::{Cli, Commands, ToolCommand, ToolNamespace};
use uv_client::{BaseClientBuilder, RegistryClient, RegistryClientBuilder};
use uv_command_support::{ExitStatus, Printer};
use uv_configuration::{
    Concurrency, Constraints, ExtrasSpecification, Reinstall, ToolRunCommand, Upgrade,
};
use uv_dispatch::{BuildDispatch, PlatformState};
use uv_distribution::LoweredExtraBuildDependencies;
use uv_distribution_types::{NameRequirementSpecification, UnresolvedRequirementSpecification};
use uv_flags::EnvironmentFlags;
use uv_preview::Preview;
use uv_pypi_types::Conflicts;
use uv_python_discovery::ConfigDiscovery;
use uv_python_interpreter::{Interpreter, PythonEnvironment};
use uv_resolve_operations::loggers::DefaultResolveLogger;
use uv_resolver::{
    DependencyMode, FlatIndex, OptionsBuilder, PythonRequirement, ResolverEnvironment,
    ResolverOutput,
};
use uv_settings::{
    Combine, EnvironmentOptions, FilesystemOptions, MalwareCheckSettings, ResolverSettings,
};
use uv_torch::TorchStrategy;
use uv_types::{BuildIsolation, HashStrategy, SourceTreeEditablePolicy};
use uv_workspace::WorkspaceCache;

/// Everything the locked run needs from uv's settings.
pub(crate) struct Context<'a> {
    pub(crate) args: ToolRunSettings,
    pub(crate) globals: GlobalSettings,
    pub(crate) malware: MalwareCheckSettings,
    pub(crate) cache: Cache,
    pub(crate) client_builder: BaseClientBuilder<'a>,
    pub(crate) workspace_cache: WorkspaceCache,
    pub(crate) printer: Printer,
}

/// Turn the parsed `uvx` command line into settings, then run.
pub(crate) async fn run(cli: Cli) -> Result<ExitStatus> {
    let Commands::Tool(ToolNamespace {
        command: ToolCommand::Uvx(uvx),
    }) = *cli.command
    else {
        bail!("expected uvx arguments");
    };

    // COPIED (trimmed) from crates/uv/src/lib.rs `run` (uv 0.13.7): the
    // parts that apply to `uvx`. Tools use user and system configuration only.
    let config_discovery = ConfigDiscovery::from_args(cli.top_level.no_config);
    let environment = EnvironmentOptions::new()?;
    let filesystem = if let Some(config_file) = cli.top_level.config_file.as_ref() {
        Some(FilesystemOptions::from_file(config_file)?)
    } else if !config_discovery.enabled() {
        None
    } else {
        FilesystemOptions::user()?.combine(FilesystemOptions::system()?)
    };
    let globals = GlobalSettings::resolve(
        &cli.top_level.global_args,
        filesystem.as_ref(),
        &environment,
        None,
    )?;
    uv_flags::init(EnvironmentFlags::from(&environment))
        .map_err(|()| anyhow::anyhow!("Flags are already initialized"))?;
    let cache_settings =
        CacheSettings::resolve(*cli.top_level.cache_args, filesystem.as_ref(), &environment);
    uv_preview::set(globals.preview)?;
    uv_preview::finalize()?;
    let printer = Printer::new(globals.quiet, globals.verbose, globals.no_progress);
    let cache = Cache::from_settings(cache_settings.no_cache, cache_settings.cache_dir)?;
    let client_builder = base_client_builder(&globals);
    // END COPIED

    // CHANGED: resolved here because `ToolRunSettings::resolve` consumes the
    // environment options (uv resolves it per command, see uv-cli settings).
    let malware = MalwareCheckSettings::resolve(filesystem.as_ref(), &environment);

    // COPIED from crates/uv/src/lib.rs, the `ToolCommand::Uvx` branch.
    let args =
        ToolRunSettings::resolve(uvx.tool_run, filesystem, ToolRunCommand::Uvx, environment)?;
    globals
        .network_settings
        .check_refresh_conflict(&args.refresh)?;
    let cache = cache.init().await?.with_refresh(
        args.refresh
            .clone()
            .combine(Refresh::from(args.settings.reinstall.clone()))
            .combine(Refresh::from(args.settings.resolver.upgrade.clone())),
    );
    let client_builder = client_builder.subcommand(vec!["uvx".to_owned()]);
    // END COPIED

    let context = Context {
        args,
        globals,
        malware,
        cache,
        client_builder,
        workspace_cache: WorkspaceCache::default(),
        printer,
    };
    crate::locked::run(context).await
}

/// COPIED from crates/uv/src/lib.rs `base_client_builder` (uv 0.13.7).
fn base_client_builder<'a>(globals: &GlobalSettings) -> BaseClientBuilder<'a> {
    let client_builder = BaseClientBuilder::new(
        globals.network_settings.connectivity,
        globals.network_settings.system_certs,
        globals.network_settings.allow_insecure_host.clone(),
        globals.preview,
        globals.network_settings.read_timeout,
        globals.network_settings.connect_timeout,
        globals.network_settings.retries,
    )
    .metadata_range_request(globals.network_settings.metadata_range_request)
    .cache_read_concurrency(globals.concurrency.cache_reads)
    .http_proxy(globals.network_settings.http_proxy.clone())
    .https_proxy(globals.network_settings.https_proxy.clone())
    .no_proxy(globals.network_settings.no_proxy.clone());
    if let Some(certificates) = &globals.network_settings.custom_certificates {
        client_builder.custom_certificates(certificates.clone())
    } else {
        client_builder
    }
}

/// The registry client for the configured indexes.
///
/// COPIED from crates/uv-environment-operations/src/lib.rs
/// `resolve_environment` (uv 0.13.7).
pub(crate) fn registry_client(
    settings: &ResolverSettings,
    client_builder: &BaseClientBuilder<'_>,
    interpreter: &Interpreter,
    cache: &Cache,
) -> Result<RegistryClient> {
    let client_builder = client_builder.clone().keyring(settings.keyring_provider);
    // Determine the PyTorch backend.
    let torch_backend = settings
        .torch_backend
        .map(|mode| {
            TorchStrategy::from_mode(
                mode,
                interpreter.platform().os(),
                settings.cuda_driver_version.clone(),
                settings.amd_gpu_architecture,
            )
        })
        .transpose()?;
    Ok(RegistryClientBuilder::new(client_builder, cache.clone())
        .index_locations(settings.index_locations.clone())
        .index_strategy(settings.index_strategy)
        .torch_backend(torch_backend)
        .markers(interpreter.markers())
        .platform(interpreter.platform())
        .build()?)
}

/// Resolve for the given interpreter, like `uv pip install` (`Direct` is
/// `--no-deps`) or `uvx` (`Transitive`, with constraints).
///
/// COPIED from crates/uv-environment-operations/src/lib.rs
/// `resolve_environment` (uv 0.13.7), the `EnvironmentResolution::Specific`
/// path. CHANGED: takes the requirements, constraints and dependency mode
/// directly; no preferences, no build constraints, no `--python-platform`.
pub(crate) async fn resolve(
    requirements: Vec<UnresolvedRequirementSpecification>,
    constraints: Vec<NameRequirementSpecification>,
    dependency_mode: DependencyMode,
    client: &RegistryClient,
    interpreter: &Interpreter,
    context: &Context<'_>,
    state: &PlatformState,
) -> Result<ResolverOutput> {
    let ResolverSettings {
        index_locations,
        index_strategy,
        keyring_provider: _,
        resolution,
        prerelease,
        fork_strategy,
        dependency_metadata,
        config_setting,
        config_settings_package,
        build_isolation,
        build_hash_checking,
        extra_build_dependencies,
        extra_build_variables,
        exclude_newer,
        link_mode,
        upgrade: _,
        build_options,
        sources,
        torch_backend: _,
        cuda_driver_version: _,
        amd_gpu_architecture: _,
    } = &context.args.settings.resolver;
    let cache = &context.cache;
    let preview: Preview = context.globals.preview;
    let build_constraints = Constraints::default();

    let tags = uv_resolve_operations::resolution_tags(None, None, interpreter)?;
    let marker_environment = uv_resolve_operations::resolution_markers(None, None, interpreter);
    let resolver_environment = ResolverEnvironment::specific(marker_environment);
    let python_requirement = PythonRequirement::from_interpreter(interpreter);

    // Determine whether to enable build isolation.
    let environment;
    let build_isolation = match build_isolation {
        uv_configuration::BuildIsolation::Isolate => BuildIsolation::Isolated,
        uv_configuration::BuildIsolation::Shared => {
            environment = PythonEnvironment::from_interpreter(interpreter.clone());
            BuildIsolation::Shared(&environment)
        }
        uv_configuration::BuildIsolation::SharedPackage(packages) => {
            environment = PythonEnvironment::from_interpreter(interpreter.clone());
            BuildIsolation::SharedPackage(&environment, packages)
        }
    };

    let options = OptionsBuilder::new()
        .resolution_mode(*resolution)
        .prerelease(prerelease.clone())
        .fork_strategy(*fork_strategy)
        .exclude_newer(exclude_newer.clone())
        .index_strategy(*index_strategy)
        .build_options(build_options.clone())
        // CHANGED: `--no-deps` for the root, as in crates/uv-pip-commands/src/install.rs.
        .dependency_mode(dependency_mode)
        .build();

    let extras = ExtrasSpecification::default();
    let groups = BTreeMap::new();
    let hasher = HashStrategy::default();
    let build_hasher = HashStrategy::from_constraints(
        &build_constraints,
        Some(&interpreter.to_resolver_marker_environment()),
        *build_hash_checking,
    )?;
    let reinstall = Reinstall::default();
    let upgrade = Upgrade::default();

    // Resolve the flat indexes from `--find-links`.
    let flat_index = FlatIndex::load(client, cache, index_locations).await?;

    // Lower the extra build dependencies, if any.
    let extra_build_requires =
        LoweredExtraBuildDependencies::from_non_lowered(extra_build_dependencies.clone())
            .into_inner();

    // Create a build dispatch.
    let resolve_dispatch = BuildDispatch::new(
        client,
        cache,
        &build_constraints,
        interpreter,
        index_locations,
        &flat_index,
        dependency_metadata,
        state.clone().into_inner(),
        *index_strategy,
        config_setting,
        config_settings_package,
        build_isolation,
        &extra_build_requires,
        extra_build_variables,
        *link_mode,
        build_options,
        &build_hasher,
        exclude_newer.clone(),
        sources.clone(),
        SourceTreeEditablePolicy::Project,
        context.workspace_cache.clone(),
        context.globals.concurrency.clone(),
        preview,
    );

    // Resolve the requirements.
    let concurrency: &Concurrency = &context.globals.concurrency;
    Ok(uv_resolve_operations::resolve(
        requirements,
        constraints,
        Vec::new(),
        Vec::new(),
        Vec::new(),
        Vec::new(),
        None,
        BTreeMap::default(),
        &extras,
        &groups,
        Vec::new(),
        None,
        &hasher,
        &reinstall,
        &upgrade,
        Some(&tags),
        resolver_environment,
        python_requirement,
        interpreter.markers(),
        Conflicts::empty(),
        client,
        &flat_index,
        state.index(),
        &resolve_dispatch,
        concurrency,
        options,
        None,
        Box::new(DefaultResolveLogger),
        context.printer,
    )
    .await?
    .0)
    // END COPIED
}
