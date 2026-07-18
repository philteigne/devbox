from __future__ import annotations

import subprocess
from pathlib import Path

from .errors import CommandError


def run(
    args: list[str],
    *,
    cwd: str | Path | None = None,
    input_text: str | None = None,
    check: bool = True,
    stream: bool = False,
) -> subprocess.CompletedProcess[str]:
    # When stream=True the child's stdout/stderr are inherited so long-running
    # commands (e.g. `docker build`) show live progress instead of appearing to
    # hang. Output is not captured in that case, so proc.stdout/stderr are None.
    try:
        proc = subprocess.run(
            args,
            cwd=str(cwd) if cwd is not None else None,
            input=input_text,
            text=True,
            capture_output=not stream,
            check=False,
        )
    except FileNotFoundError as exc:
        raise CommandError(f"`{args[0]}` is not installed or is not on PATH.") from exc

    if check and proc.returncode != 0:
        details = (proc.stderr or proc.stdout or "").strip()
        if details:
            raise CommandError(f"`{' '.join(args)}` failed: {details}")
        raise CommandError(f"`{' '.join(args)}` failed with exit code {proc.returncode}.")
    return proc


def output(args: list[str], *, cwd: str | Path | None = None) -> str:
    return run(args, cwd=cwd).stdout.strip()

