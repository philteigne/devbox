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
) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.run(
            args,
            cwd=str(cwd) if cwd is not None else None,
            input=input_text,
            text=True,
            capture_output=True,
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

