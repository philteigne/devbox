from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .errors import DevboxError
from .proc import output


GITHUB_REMOTE_PATTERNS = [
    re.compile(r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"),
    re.compile(r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$"),
    re.compile(r"^ssh://git@github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"),
]


@dataclass(frozen=True)
class RepoContext:
    root: Path
    owner: str
    repo: str
    origin_url: str


def resolve_repo(path: str | Path) -> RepoContext:
    start = Path(path).expanduser().resolve()
    try:
        root = Path(output(["git", "rev-parse", "--show-toplevel"], cwd=start)).resolve()
    except DevboxError as exc:
        raise DevboxError(f"`{start}` is not inside a git work tree.") from exc

    try:
        origin = output(["git", "remote", "get-url", "origin"], cwd=root)
    except DevboxError as exc:
        raise DevboxError("origin remote is missing or cannot be read.") from exc

    parsed = parse_github_origin(origin)
    if parsed is None:
        raise DevboxError(
            "origin remote must be a GitHub URL like "
            "https://github.com/<owner>/<repo>.git, git@github.com:<owner>/<repo>.git, "
            "or ssh://git@github.com/<owner>/<repo>.git."
        )
    owner, repo = parsed
    return RepoContext(root=root, owner=owner, repo=repo, origin_url=origin)


def parse_github_origin(url: str) -> tuple[str, str] | None:
    for pattern in GITHUB_REMOTE_PATTERNS:
        match = pattern.match(url.strip())
        if match:
            return match.group("owner"), match.group("repo")
    return None

