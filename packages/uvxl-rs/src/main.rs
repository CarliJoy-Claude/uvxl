//! `uvxl-rs`: like `uvx`, but the package is installed with the lock its
//! `Pylock` Project-URL links, verified against the configured indexes.
//!
//! Glue copied from uv is marked `COPIED from <file> (uv 0.13.7)`; the rest
//! is the PoC's own logic (rule IDs as in `docs/spec.md`).

use std::ffi::OsString;
use std::process::ExitCode;

use clap::Parser;
use uv_cli::Cli;

mod glue;
mod lock;
mod locked;
mod pylock_url;

fn main() -> ExitCode {
    // Parse our arguments exactly like `uvx`: uv's own `uvx` binary re-runs
    // `uv tool uvx ...`, so we hand clap the same argument vector.
    let args = [OsString::from("uv"), "tool".into(), "uvx".into()]
        .into_iter()
        .chain(std::env::args_os().skip(1));
    let cli = match Cli::try_parse_from(args) {
        Ok(cli) => cli,
        Err(err) => err.exit(),
    };

    // COPIED from crates/uv/src/lib.rs `main` (uv 0.13.7): a large stack and
    // a current-thread runtime on its own thread.
    let min_stack_size = uv_threads::min_stack_size();
    let main2 = move || {
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .thread_stack_size(min_stack_size)
            .build()
            .expect("Failed building the Runtime");
        // Box the large main future to avoid stack overflows.
        let result = runtime.block_on(Box::pin(glue::run(cli)));
        runtime.shutdown_background();
        result
    };
    let result = std::thread::Builder::new()
        .name("main2".to_owned())
        .stack_size(min_stack_size)
        .spawn(main2)
        .expect("Tokio executor failed, was there a panic?")
        .join()
        .expect("Tokio executor failed, was there a panic?");
    // END COPIED

    match result {
        Ok(status) => status.into(),
        Err(err) => {
            #[expect(clippy::print_stderr)]
            {
                eprintln!("error: {err:#}");
            }
            ExitCode::from(1)
        }
    }
}
