from __future__ import annotations

from ..core import config
from ..core.gitctx import resolve_repo


def run(path: str = ".") -> int:
    ctx = resolve_repo(path)
    project = config.read_project_config(ctx.owner, ctx.repo)
    if project is None:
        print(
            f"devbox not initialized for `{ctx.owner}/{ctx.repo}` -- will run in "
            "NO-PR mode (local only; you push manually)."
        )
        return 0
    if project.pr_eligible:
        print(
            f"devbox initialized for `{ctx.owner}/{ctx.repo}` -- will run in PR mode "
            "(agent can open pull requests)."
        )
        return 0
    if project.branch_protection == "unavailable":
        print(
            "devbox initialized, but the default branch is not protected -- PR mode is "
            "disabled for safety; will run in NO-PR mode."
        )
        return 0
    if project.app_repo_access == "missing":
        print(
            "devbox initialized, but the App installation doesn't include this repo -- "
            "PR mode is disabled; add the repo and re-run `devbox init`. Will run in NO-PR mode."
        )
        return 0
    print(
        f"devbox config for `{ctx.owner}/{ctx.repo}` is invalid -- will run in "
        "NO-PR mode (local only; you push manually)."
    )
    return 0

