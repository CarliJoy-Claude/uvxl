"""Command line entry points (click).

Errors exit with 1 (usage errors with 2); ``uvxl-py`` passes on the exit code
of the command it runs.
"""

import subprocess
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import click

from uvxl_py.errors import VerifyError
from uvxl_py.lock import verify_entries
from uvxl_py.root import parse_root_request
from uvxl_py.run import prepare
from uvxl_py.uv import Uv, find_interpreter, find_uv


@contextmanager
def reported_errors() -> Iterator[None]:
    """Turn our errors into a click error: message on stderr, exit 1."""
    try:
        yield
    except VerifyError as error:
        raise click.ClickException(str(error)) from error


def make_uv(
    python: str | None,
    global_args: Sequence[str] = (),
    resolver_args: Sequence[str] = (),
) -> Uv:
    executable = find_uv()
    interpreter = find_interpreter(executable, python, global_args)
    return Uv(executable, interpreter, tuple(global_args), tuple(resolver_args))


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
# uvxl-py
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UvOption:
    """A ``uvx`` option that the Python backend passes to uv unchanged."""

    flags: tuple[str, ...]
    # "flag" (no value), "count" (repeatable flag), "value" or "values".
    kind: str
    # Every uv call (False) or only the ``uv pip`` calls (True).
    resolver: bool

    @property
    def name(self) -> str:
        return self.flags[-1].removeprefix("--").replace("-", "_")


# The uvx options both backends support, with uvx's names and meaning.
PASS_THROUGH: Final = (
    UvOption(("--index",), "values", resolver=True),
    UvOption(("--default-index",), "value", resolver=True),
    UvOption(("-i", "--index-url"), "value", resolver=True),
    UvOption(("--extra-index-url",), "values", resolver=True),
    UvOption(("-f", "--find-links"), "values", resolver=True),
    UvOption(("--no-index",), "flag", resolver=True),
    UvOption(("--index-strategy",), "value", resolver=True),
    UvOption(("--keyring-provider",), "value", resolver=True),
    UvOption(("--resolution",), "value", resolver=True),
    UvOption(("--prerelease",), "value", resolver=True),
    UvOption(("--exclude-newer",), "value", resolver=True),
    UvOption(("-n", "--no-cache"), "flag", resolver=False),
    UvOption(("--cache-dir",), "value", resolver=False),
    UvOption(("--offline",), "flag", resolver=False),
    UvOption(("--allow-insecure-host",), "values", resolver=False),
    UvOption(("--system-certs",), "flag", resolver=False),
    UvOption(("--config-file",), "value", resolver=False),
    UvOption(("--no-config",), "flag", resolver=False),
    UvOption(("--managed-python",), "flag", resolver=False),
    UvOption(("--no-managed-python",), "flag", resolver=False),
    UvOption(("--no-python-downloads",), "flag", resolver=False),
    UvOption(("-q", "--quiet"), "count", resolver=False),
    UvOption(("-v", "--verbose"), "count", resolver=False),
)

# uvx options that would bypass the lock: refused, as by the Rust backend.
REFUSED: Final = (
    ("-w", "--with"),
    ("--with-editable",),
    ("--with-requirements",),
    ("-c", "--constraints"),
    ("--overrides",),
)


def uvx_options[F: Callable[..., None]](function: F) -> F:
    """Add the pass-through and refused uvx options to a click command."""
    for spec in reversed(PASS_THROUGH):
        envvar = None
        if spec.name == "allow_insecure_host":
            # uv reads the variable itself; the lock URL check needs it too.
            envvar = "UV_INSECURE_HOST"
        if spec.kind == "flag":
            option = click.option(*spec.flags, spec.name, is_flag=True)
        elif spec.kind == "count":
            option = click.option(*spec.flags, spec.name, count=True)
        else:
            multiple = spec.kind == "values"
            option = click.option(
                *spec.flags, spec.name, multiple=multiple, envvar=envvar
            )
        function = option(function)
    for flags in reversed(REFUSED):
        function = click.option(*flags, multiple=True, hidden=True)(function)
    return function


def uv_arguments(options: Mapping[str, object], resolver: bool) -> tuple[str, ...]:
    """The uv arguments for the pass-through options of one group."""
    arguments: list[str] = []
    for spec in PASS_THROUGH:
        value = options[spec.name]
        flag = spec.flags[-1]
        if spec.resolver != resolver:
            continue
        if spec.kind == "flag" and value:
            arguments.append(flag)
        elif spec.kind == "count" and isinstance(value, int):
            arguments.extend((flag,) * value)
        elif spec.kind == "values" and isinstance(value, tuple):
            for item in value:
                arguments.extend((flag, str(item)))
        elif spec.kind == "value" and value is not None:
            arguments.extend((flag, str(value)))
    return tuple(arguments)


def refuse_lock_bypasses(options: Mapping[str, object]) -> None:
    for flags in REFUSED:
        if options[flags[-1].removeprefix("--").replace("-", "_")]:
            raise click.ClickException(
                f"{flags[-1]} is not supported with a locked run"
            )


@click.command(
    name="uvxl-py",
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
    "-p",
    "--python",
    metavar="REQUEST",
    help="Target interpreter, as understood by `uv python find`.",
)
@uvx_options
def uvxl_main(
    *,
    command: str,
    args: tuple[str, ...],
    from_: str | None,
    python: str | None,
    **options: object,
) -> None:
    """Run COMMAND from a package installed with the lock its metadata links.

    Like `uvx`, with uvx's option names, but the package must declare a
    `Pylock` Project-URL; the lock is verified against the configured indexes
    before anything is installed. Options other than `--from` and `--python`
    go to uv unchanged.
    """
    refuse_lock_bypasses(options)
    if from_ is None:
        requirement = command
    else:
        requirement = from_
    hosts = options["allow_insecure_host"]
    insecure_hosts: tuple[str, ...] = ()
    if isinstance(hosts, tuple):
        insecure_hosts = tuple(str(host) for host in hosts)
    with reported_errors():
        request = parse_root_request(requirement)
        uv = make_uv(
            python, uv_arguments(options, resolver=False), uv_arguments(options, True)
        )
        prepared = prepare(uv, request, insecure_hosts)
    if from_ is None:
        command = request.raw_name
    environment = prepared.environment
    state = "reused"
    if environment.created:
        state = "created"
    click.echo(
        f"uvxl-py: {prepared.root.name} {prepared.root.version}, "
        f"{prepared.fetched.lock_url.mode} lock {prepared.fetched.lock_url.url}, "
        f"environment {state}: {environment.path}",
        err=True,
    )
    path = environment.executable(command)
    if not path.is_file():
        raise click.ClickException(f"{prepared.root.name} provides no {command!r}")
    completed = subprocess.run((str(path), *args), check=False)
    raise SystemExit(completed.returncode)
