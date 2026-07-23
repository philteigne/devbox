from __future__ import annotations

import argparse
import sys

from .commands import check, init, start
from .core.errors import DevboxError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="devbox",
        description="Launch an isolated Docker devbox for a GitHub repository.",
    )
    parser.add_argument("--version", action="store_true", help="print version and exit")
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser("init", help="enable PR mode for a personal repository")
    init_parser.add_argument("path", nargs="?", default=".", help="path inside the target repository")

    check_parser = subparsers.add_parser("check", help="show cached devbox mode for a repository")
    check_parser.add_argument("path", nargs="?", default=".", help="path inside the target repository")

    start_parser = subparsers.add_parser("start", help="start or reuse the repository devbox")
    start_parser.add_argument("path", nargs="?", default=".", help="path inside the target repository")
    start_parser.add_argument("--no-pr", action="store_true", help="force NO-PR mode")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        from . import __version__

        print(f"devbox {__version__}")
        return 0

    if not args.command:
        parser.print_help()
        return 2

    try:
        if args.command == "init":
            return init.run(args.path)
        if args.command == "check":
            return check.run(args.path)
        if args.command == "start":
            return start.run(args.path, no_pr=args.no_pr)
    except DevboxError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code

    parser.error(f"unknown command: {args.command}")
    return 2
