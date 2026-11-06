"""Command line entry points (click).

Errors exit with 1 (usage errors with 2); ``uvlx`` passes on the exit code of
the command it runs.

Tests pass ``obj=FetchPolicy(allow_http=True)`` to reach a local index;
there is deliberately no command line flag for that.
"""

import subprocess
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import click

from uvlx.clean import clean, render
from uvlx.environments import default_cache_dir
from uvlx.errors import VerifyError
from uvlx.fetch import FetchPolicy
from uvlx.lock import is_pylock_name
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


def make_uv(uv_path: str | None, python: str | None, uv_args: Sequence[str]) -> Uv:
    executable = find_uv(uv_path)
    return Uv(executable, find_interpreter(executable, python), tuple(uv_args))


# --------------------------------------------------------------------------
# pylock-verify-clean
# --------------------------------------------------------------------------


@click.command(
    name="pylock-verify-clean",
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.argument("wheel", type=click.Path(path_type=Path, dir_okay=False))
@click.argument("lock", type=click.Path(path_type=Path, dir_okay=False))
@click.argument("uv_args", nargs=-1, type=click.UNPROCESSED, metavar="[-- UV_ARGS]...")
@click.option(
    "--extra",
    "extras",
    multiple=True,
    metavar="NAME",
    help="An extra of the root wheel to install; repeat for several.",
)
@click.option(
    "--python",
    metavar="REQUEST",
    help="Target interpreter, as understood by `uv python find`.",
)
@click.option("--uv", "uv_path", metavar="PATH", help="The uv executable to use.")
@click.option(
    "-o",
    "--output",
    type=click.Path(path_type=Path, dir_okay=False),
    help="Write the cleaned lock here instead of stdout.",
)
@click.option(
    "--install",
    is_flag=True,
    help="Install the cleaned lock into the target interpreter with uv.",
)
def verify_clean_main(  # noqa: PLR0913
    *,
    wheel: Path,
    lock: Path,
    uv_args: tuple[str, ...],
    extras: tuple[str, ...],
    python: str | None,
    uv_path: str | None,
    output: Path | None,
    install: bool,
) -> None:
    """Verify LOCK for the root WHEEL with uv and write a cleaned lock.

    Arguments after `--` are passed to every uv pip call, for example
    `-- --index-url URL`.
    """
    if output is not None and not is_pylock_name(output.name):
        raise click.UsageError(
            f"output name {output.name!r} must be pylock.toml or "
            "pylock.<name>.toml (PEP 751), or installers will refuse it"
        )
    with reported_errors(), tempfile.TemporaryDirectory() as directory:
        uv = make_uv(uv_path, python, uv_args)
        result = clean(wheel, lock, uv, extras)
        text = render(result)
        cleaned = Path(directory) / "pylock.toml"
        if output is not None:
            cleaned = output
        cleaned.write_text(text, encoding="utf-8")
        if install:
            uv.install(cleaned)
    if output is None and not install:
        click.echo(text, nl=False)
    click.echo(
        f"ok: {len(result.packages)} package(s) in the cleaned lock; "
        f"lock entries not needed: [{', '.join(result.unreached)}]",
        err=True,
    )


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
@click.option("--uv", "uv_path", metavar="PATH", help="The uv executable to use.")
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
    uv_path: str | None,
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
        uv = make_uv(uv_path, python, uv_args)
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
