"""Command line entry points (click).

Exit codes: 0 ok, 1 lock rejected, 2 uv, network or usage error.
"""

import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

import click

from uvlx.clean import CleanResult, clean, render
from uvlx.errors import LockRejected, UvError, VerifyError
from uvlx.lock import is_pylock_name
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
    except UvError as error:
        click.echo(f"error: {error}", err=True)
        sys.exit(EXIT_ERROR)
    except VerifyError as error:
        click.echo(f"rejected: {error}", err=True)
        sys.exit(EXIT_REJECTED)


def make_uv(uv_path: str | None, python: str | None, uv_args: Sequence[str]) -> Uv:
    executable = find_uv(uv_path)
    return Uv(executable, find_interpreter(executable, python), tuple(uv_args))


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
