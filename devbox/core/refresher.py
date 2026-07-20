from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from . import github_app
from .docker import inspect_container, is_running
from .token_file import write_atomic


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh a devbox GitHub App token.")
    parser.add_argument("--installation-id", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--container", required=True)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--pid-file", required=True)
    parser.add_argument("--interval-seconds", type=int, default=3000)
    args = parser.parse_args(argv)

    Path(args.pid_file).parent.mkdir(parents=True, exist_ok=True)
    with Path(args.pid_file).open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(str(os.getpid()) + "\n")

    identity = github_app.load_identity()
    token_file = Path(args.token_file)
    while True:
        container = inspect_container(args.container)
        if not container or not is_running(container):
            break
        token = github_app.create_installation_token(identity, args.installation_id, args.repo)["token"]
        write_atomic(token_file, token)
        time.sleep(args.interval_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
