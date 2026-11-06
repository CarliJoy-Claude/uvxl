#!/usr/bin/env bash
# The whole test suite against the built distributions in dist/, installed
# into a fresh environment (not the editable workspace packages).
# Needs the wheels of uvxl, uvxl-py and uvxl-rs for this platform in dist/:
# locally from the `build` and `build-rust-wheel` hooks, in CI from the
# build jobs. The Python version follows uv's defaults (UV_PYTHON in CI).
#
# Usage: scripts/test-wheels.sh [PYTEST ARGS...]
set -euo pipefail
cd "$(dirname "$0")/.."

venv=build/test-wheels-venv
uv venv --clear "$venv"
if [ -d "$venv/Scripts" ]; then
    bin="$venv/Scripts"
else
    bin="$venv/bin"
fi
# The environment's own path: prek runs under `uv run`, whose VIRTUAL_ENV
# must not leak into the tests (native path form, also on Windows).
VIRTUAL_ENV=$("$bin/python" -c 'import sys; print(sys.prefix)')
export VIRTUAL_ENV
export PATH="$PWD/$bin:$PATH"
uv pip install dist/uvxl-*.whl dist/uvxl_py-*.whl dist/uvxl_rs-*.whl
uv pip install pytest tomli-w

# The wheels are what is installed and the commands work.
uv pip list
python -c "import uvxl, uvxl_py; print(uvxl.__file__, uvxl_py.__file__)"
python -c "import uvxl; assert uvxl.find_rust(), 'uvxl-rs missing'"
uvxl --help > /dev/null
pylock-verify --help > /dev/null

python -m pytest -ra "$@"
