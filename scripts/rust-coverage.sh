#!/usr/bin/env bash
# Rust coverage of uvxl-rs, measured from the pytest suite (no Rust test
# knowledge needed). The Rust unit tests run separately (`cargo test`): their
# test binary contains every function, which would count as uncovered here.
#
# Builds an instrumented uvxl-rs, puts it where `uvxl` finds the backend
# (the environment's scripts directory), runs the tests, and reports.
# The PoC's own code must be fully covered; glue.rs and main.rs (copied
# from uv, or adapting uv's types) are reported but not gated.
#
# Afterwards `uv sync --reinstall-package uvxl-rs` restores the normal binary.
set -euo pipefail
cd "$(dirname "$0")/.."
manifest=packages/uvxl-rs/Cargo.toml
# A target directory of its own: the instrumentation comes from a rustc
# wrapper that cargo's change detection does not see, so a shared directory
# would reuse uninstrumented builds.
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-packages/uvxl-rs/target}/llvm-cov-target"

eval "$(cargo llvm-cov show-env --sh --manifest-path "$manifest")"
# Only the measurements: the target directory is shared with normal builds.
cargo llvm-cov clean --profraw-only --manifest-path "$manifest"
cargo build --manifest-path "$manifest"

target="${CARGO_LLVM_COV_TARGET_DIR}/debug/uvxl-rs"
scripts=$(uv run --no-sync python -c 'import sysconfig; print(sysconfig.get_path("scripts"))')
cp "$target" "$scripts/uvxl-rs"

uv run --no-sync pytest -q "$@"

cargo llvm-cov report --manifest-path "$manifest" \
    --ignore-filename-regex '(glue|main)\.rs$' \
    --show-missing-lines --fail-under-lines 100
