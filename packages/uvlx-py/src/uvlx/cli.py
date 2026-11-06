"""Command line entry points (click).

Errors exit with 1 (usage errors with 2); ``uvlx`` passes on the exit code of
the command it runs.

Tests pass ``obj=FetchPolicy(allow_http=True)`` to reach a local index;
there is deliberately no command line flag for that.
"""

import subprocess
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import click

from uvlx.environments import default_cache_dir
from uvlx.errors import VerifyError
from uvlx.fetch import FetchPolicy
from uvlx.lock import verify_entries
from uvlx.root import parse_root_request
from uvlx.run import prepare
from uvlx.uv import Uv, find_interpreter, find_uv


@contextmanager
def reported_errors() -> Iterator[None]:
    """Turn our errors into a click error: message on stderr, exit 1."""
    try:
        yield
    except VerifyError as error:
        raise click.ClickException(str(error)) from error


def make_uv(python: str | None, uv_args: Sequence[str]) -> Uv:
    executable = find_uv()
    return Uv(executable, find_interpreter(executable, python), tuple(uv_args))


# --------------------------------------------------------------------------
# pylock-verify
# --------------------------------------------------------------------------


@click.command(
    name="pylock-verify",
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.argument("lock", type=click.Path(path_type=Path, dir_okay=False))
@click.argument("package")
def verify_main(lock: Path, package: str) -> None:
    """Check that LOCK only has entries a locked install of PACKAGE accepts.

    For maintainers before publishing a lock: registry wheels and sdists with
    hashes, plus one directory entry for PACKAGE itself (as `uv export`
    writes it). Dependencies, versions and indexes are checked at install.
    """
    with reported_errors():
        count = verify_entries(lock.name, lock.read_bytes(), package)
    click.echo(f"ok: {count} entries allowed")


# --------------------------------------------------------------------------
# uvlx
# --------------------------------------------------------------------------


@click.command(
    name="uvlx",
    context_settings={
        "allow_interspersed_args": False,
        "help_option_names": ["-h", "--help"],
    },
)
@click.argument("command")
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
@click.option(
    "--from",
    "from_",
    metavar="REQUIREMENT",
    help="The package providing COMMAND, like `app[extra]==1.0`. "
    "Default: COMMAND itself.",
)
@click.option(
    "--python",
    metavar="REQUEST",
    help="Target interpreter, as understood by `uv python find`.",
)
@click.option(
    "--uv-arg",
    "uv_args",
    multiple=True,
    metavar="ARG",
    help="An argument for every uv pip call, like `--uv-arg=--index-url=URL`; "
    "repeatable. uv's own settings and UV_* variables apply as usual.",
)
@click.option(
    "--cache-dir",
    type=click.Path(path_type=Path, file_okay=False),
    help="Where environments are kept. Default: $XDG_CACHE_HOME/uvlx or ~/.cache/uvlx.",
)
@click.pass_context
def uvlx_main(  # noqa: PLR0913
    context: click.Context,
    *,
    command: str,
    args: tuple[str, ...],
    from_: str | None,
    python: str | None,
    uv_args: tuple[str, ...],
    cache_dir: Path | None,
) -> None:
    """Run COMMAND from a package installed with the lock its metadata links.

    Like `uvx`, but the package must declare a `Pylock` Project-URL; the lock
    is verified against the configured indexes before anything is installed.
    """
    policy = context.find_object(FetchPolicy)
    if policy is None:
        policy = FetchPolicy()
    if cache_dir is None:
        cache_dir = default_cache_dir()
    if from_ is None:
        requirement = command
    else:
        requirement = from_
    with reported_errors():
        request = parse_root_request(requirement)
        uv = make_uv(python, uv_args)
        prepared = prepare(uv, request, cache_dir, policy)
    if from_ is None:
        command = request.raw_name
    environment = prepared.environment
    state = "reused"
    if environment.created:
        state = "created"
    click.echo(
        f"uvlx: {prepared.root.name} {prepared.root.version}, "
        f"{prepared.fetched.lock_url.mode} lock {prepared.fetched.lock_url.url}, "
        f"environment {state}: {environment.path}",
        err=True,
    )
    path = environment.executable(command)
    if not path.is_file():
        raise click.ClickException(f"{prepared.root.name} provides no {command!r}")
    completed = subprocess.run((str(path), *args), check=False)
    context.exit(completed.returncode)
