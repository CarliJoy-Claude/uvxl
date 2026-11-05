"""Command line entry points.

Exit codes: 0 ok, 1 lock rejected, 2 uv, network or usage error.
"""

import argparse
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from uvlx.clean import clean, render
from uvlx.errors import LockRejected, UvError, VerifyError
from uvlx.lock import is_pylock_name
from uvlx.uv import Uv, find_interpreter, find_uv


def build_verify_clean_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pylock-verify-clean",
        description="Verify a pylock.toml for a root wheel with uv and write a "
        "cleaned lock. Arguments after `--` are passed to every uv pip call "
        "(for example `-- --index-url URL`).",
    )
    parser.add_argument(
        "wheel", type=Path, help="the root wheel chosen by the installer"
    )
    parser.add_argument(
        "lock", type=Path, help="the lock file (pylock.toml or pylock.<name>.toml)"
    )
    parser.add_argument(
        "--extra",
        action="append",
        dest="extras",
        metavar="NAME",
        help="an extra of the root wheel to install; repeat for several",
    )
    parser.add_argument(
        "--python",
        metavar="REQUEST",
        help="target interpreter, as understood by `uv python find`",
    )
    parser.add_argument("--uv", metavar="PATH", help="the uv executable to use")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="write the cleaned lock here instead of stdout",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="install the cleaned lock into the target interpreter with uv",
    )
    return parser


def split_uv_args(argv: Sequence[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if "--" in argv:
        position = list(argv).index("--")
        return tuple(argv[:position]), tuple(argv[position + 1 :])
    return tuple(argv), ()


def write_output(text: str, output: Path | None) -> None:
    if output is None:
        sys.stdout.write(text)
    else:
        output.write_text(text, encoding="utf-8")


def verify_clean_main(argv: Sequence[str] | None = None) -> int:
    """``pylock-verify-clean``: verify a lock for a local root wheel."""
    if argv is None:
        argv = sys.argv[1:]
    own_args, uv_args = split_uv_args(argv)
    args = build_verify_clean_parser().parse_args(own_args)
    if args.output is not None and not is_pylock_name(args.output.name):
        print(
            f"error: output name {args.output.name!r} must be pylock.toml "
            "or pylock.<name>.toml (PEP 751), or installers will refuse it",
            file=sys.stderr,
        )
        return 2
    try:
        executable = find_uv(args.uv)
        uv = Uv(executable, find_interpreter(executable, args.python), uv_args)
        result = clean(args.wheel, args.lock, uv, tuple(args.extras or ()))
        text = render(result)
        if args.install and args.output is None:
            with tempfile.TemporaryDirectory() as directory:
                lock = Path(directory) / "pylock.toml"
                lock.write_text(text, encoding="utf-8")
                uv.install(lock)
        else:
            write_output(text, args.output)
            if args.install and args.output is not None:
                uv.install(args.output)
    except LockRejected as error:
        print(f"rejected: {error}", file=sys.stderr)
        return 1
    except UvError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except VerifyError as error:
        print(f"rejected: {error}", file=sys.stderr)
        return 1
    summary = (
        f"ok: {len(result.packages)} package(s) in the cleaned lock; "
        f"{len(result.unreached)} lock entr(ies) not needed"
    )
    if result.unreached:
        summary += f": {', '.join(result.unreached)}"
    if args.install:
        summary += "; installed"
    print(summary, file=sys.stderr)
    return 0
