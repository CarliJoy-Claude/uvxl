"""Command line entry points (click).

Exit codes: 0 ok, 1 lock rejected, 2 uv, network or usage error.
``uvlx`` exits with the command's own exit code once the command runs.

Tests pass ``obj=FetchPolicy(allow_http=True)`` to reach a local index;
there is deliberately no command line flag for that.
"""

import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

import click

from uvlx.clean import CleanResult, clean, render
from uvlx.environments import default_cache_dir
from uvlx.errors import FetchError, LockRejected, UvError, VerifyError
from uvlx.fetch import FetchPolicy
from uvlx.lock import is_pylock_name
from uvlx.root import parse_root_request
from uvlx.run import Prepared, prepare
from uvlx.uv import Uv, find_interpreter, find_uv

EXIT_REJECTED = 1
EXIT_ERROR = 2


def guarded[T](action: Callable[[], T]) -> T:
    """Run ``action``; report our errors on stderr and exit with their code."""
    try:
        return action()
    except LockRejected as error:
        click.echo(f"rejected: {error}", err=True)
        sys.exit(EXIT_REJECTED)
    except (UvError, FetchError) as error:
        click.echo(f"error: {error}", err=True)
        sys.exit(EXIT_ERROR)
    except VerifyError as error:
        click.echo(f"rejected: {error}", err=True)
        sys.exit(EXIT_REJECTED)


def make_uv(uv_path: str | None, python: str | None, uv_args: Sequence[str]) -> Uv:
    executable = find_uv(uv_path)
    return Uv(executable, find_interpreter(executable, python), tuple(uv_args))


# --------------------------------------------------------------------------
# pylock-verify-clean
# --------------------------------------------------------------------------


def write_and_install(
    result: CleanResult, uv: Uv, output: Path | None, install: bool
) -> None:
    text = render(result)
    if output is None and install:
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "pylock.toml"
            lock.write_text(text, encoding="utf-8")
            uv.install(lock)
        return
    if output is None:
        click.echo(text, nl=False)
    else:
        output.write_text(text, encoding="utf-8")
    if install and output is not None:
        uv.install(output)


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

    def action() -> CleanResult:
        uv = make_uv(uv_path, python, uv_args)
        result = clean(wheel, lock, uv, extras)
        write_and_install(result, uv, output, install)
        return result

    result = guarded(action)
    summary = (
        f"ok: {len(result.packages)} package(s) in the cleaned lock; "
        f"{len(result.unreached)} lock entr(ies) not needed"
    )
    if result.unreached:
        summary += f": {', '.join(result.unreached)}"
    if install:
        summary += "; installed"
    click.echo(summary, err=True)


# --------------------------------------------------------------------------
# uvlx
# --------------------------------------------------------------------------


def describe(prepared: Prepared) -> str:
    lock_url = prepared.fetched.lock_url
    if prepared.environment.created:
        state = "created"
    else:
        state = "reused"
    return (
        f"uvlx: {prepared.root.name} {prepared.root.version} with "
        f"{lock_url.mode} lock {lock_url.url} "
        f"({len(prepared.result.packages)} package(s)); "
        f"environment {state}: {prepared.environment.path}"
    )


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
@click.option("--index-url", metavar="URL", help="Passed to uv.")
@click.option(
    "--extra-index-url", multiple=True, metavar="URL", help="Passed to uv; repeatable."
)
@click.option(
    "--uv-arg",
    "uv_extra",
    multiple=True,
    metavar="ARG",
    help="Any other argument for every uv pip call; repeatable.",
)
@click.option(
    "--cache-dir",
    type=click.Path(path_type=Path, file_okay=False),
    help="Where environments are kept. Default: $UVLX_CACHE_DIR, "
    "$XDG_CACHE_HOME/uvlx or ~/.cache/uvlx.",
)
@click.option(
    "--allow-unknown-fragment",
    is_flag=True,
    help="Ignore unknown fragment parameters of the Pylock URL (overrides M4).",
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
    index_url: str | None,
    extra_index_url: tuple[str, ...],
    uv_extra: tuple[str, ...],
    cache_dir: Path | None,
    allow_unknown_fragment: bool,
) -> None:
    """Run COMMAND from a package installed with the lock its metadata links.

    Like `uvx`, but the package must declare a `Pylock` Project-URL; the lock
    is verified against the configured indexes before anything is installed.
    """
    policy = context.find_object(FetchPolicy)
    if policy is None:
        policy = FetchPolicy()
    uv_args: list[str] = []
    if index_url is not None:
        uv_args.extend(("--index-url", index_url))
    for url in extra_index_url:
        uv_args.extend(("--extra-index-url", url))
    uv_args.extend(uv_extra)
    if cache_dir is None:
        cache_dir = default_cache_dir()
    resolved_cache = cache_dir

    def action() -> tuple[Prepared, str]:
        if from_ is None:
            request = parse_root_request(command)
            executable = request.raw_name
        else:
            request = parse_root_request(from_)
            executable = command
        uv = make_uv(uv_path, python, uv_args)
        prepared = prepare(
            uv,
            request,
            resolved_cache,
            policy,
            allow_unknown_fragment=allow_unknown_fragment,
        )
        return prepared, executable

    prepared, executable = guarded(action)
    click.echo(describe(prepared), err=True)
    path = prepared.environment.executable(executable)
    if not path.is_file():
        click.echo(
            f"error: {prepared.root.name} provides no command {executable!r} "
            f"(looked for {path})",
            err=True,
        )
        sys.exit(EXIT_ERROR)
    completed = subprocess.run((str(path), *args), check=False)
    sys.exit(completed.returncode)
