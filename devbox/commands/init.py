from __future__ import annotations

from ..core import branch_protection, config, gh, github_app
from ..core.config import ProjectConfig
from ..core.errors import DevboxError, GitHubApiError
from ..core.gitctx import resolve_repo


def run(path: str = ".") -> int:
    ctx = resolve_repo(path)
    user_token = gh.require_gh_auth()
    login = gh.user(user_token).get("login", "")
    if ctx.owner.casefold() != str(login).casefold():
        raise DevboxError(
            "devbox only initializes repositories owned by your personal account for PR mode. "
            f"`{ctx.owner}` is not `{login}`. Repositories owned by organizations or other "
            "accounts stay in NO-PR mode; run `devbox start` (or `devbox start --no-pr`) instead."
        )

    identity = github_app.load_identity()
    app = github_app.get_app(identity)
    slug = app.get("slug", "")
    if not slug:
        raise DevboxError("GitHub App response did not include a slug.")
    identity = github_app.save_derived_identity(identity, slug)

    installation = _find_installation(identity, login)
    installation_id = str(installation["id"])
    repository_selection = installation.get("repository_selection", "")

    repo_data = gh.repo(user_token, ctx.owner, ctx.repo)
    repo_id = str(repo_data.get("id", ""))
    default_branch = str(repo_data.get("default_branch") or "main")

    if not _ensure_app_repo_access(identity, installation_id, repository_selection, user_token, ctx.owner, ctx.repo, repo_id):
        config.mark_app_repo_missing(ctx.owner, ctx.repo)
        url = f"https://github.com/settings/installations/{installation_id}"
        raise DevboxError(
            "The devbox GitHub App installation does not include this repo. "
            f"Add it here: {url}, then re-run `devbox init`."
        )

    protection_state = _ensure_branch_protection(user_token, ctx.owner, ctx.repo, default_branch)
    project = ProjectConfig(
        owner=ctx.owner,
        repo=ctx.repo,
        repo_id=repo_id,
        default_branch=default_branch,
        installation_id=installation_id,
        branch_protection=protection_state,
        app_repo_access="granted",
    )
    path_written = config.write_project_config(project)
    if project.pr_eligible:
        print(f"devbox initialized for `{ctx.owner}/{ctx.repo}` in PR mode.")
    else:
        print(
            "devbox initialized, but PR mode is disabled for safety because default "
            "branch protection could not be enforced."
        )
    print(f"Wrote {path_written}")
    return 0


def _find_installation(identity: github_app.AppIdentity, login: str) -> dict:
    installations = github_app.installations(identity)
    for installation in installations:
        account = installation.get("account") or {}
        if str(account.get("login", "")).casefold() == login.casefold():
            return installation
    slug = identity.app_slug or github_app.get_app(identity).get("slug", "")
    raise DevboxError(
        "The devbox GitHub App is not installed on your account. "
        f"Install it: https://github.com/apps/{slug}/installations/new then re-run `devbox init`."
    )


def _ensure_app_repo_access(
    identity: github_app.AppIdentity,
    installation_id: str,
    repository_selection: str,
    user_token: str,
    owner: str,
    repo_name: str,
    repo_id: str,
) -> bool:
    if github_app.verify_repo_access(identity, installation_id, repo_name):
        return True
    if repository_selection != "selected":
        return False

    pat = gh.classic_pat()
    if pat:
        try:
            gh.add_repo_to_installation(pat, installation_id, repo_id)
        except GitHubApiError as exc:
            print(f"Could not auto-add repo to GitHub App installation: {exc}")
    else:
        print(f"Add the repo to the devbox GitHub App installation: https://github.com/settings/installations/{installation_id}")

    return github_app.verify_repo_access(identity, installation_id, repo_name)


def _ensure_branch_protection(user_token: str, owner: str, repo_name: str, default_branch: str) -> str:
    repo_data = gh.repo(user_token, owner, repo_name)
    permissions = repo_data.get("permissions") or {}
    if permissions.get("admin") is not True:
        print("PR mode disabled: the authenticated GitHub user is not an admin on this repo.")
        return "unavailable"

    try:
        existing = gh.branch_protection(user_token, owner, repo_name, default_branch)
        payload = branch_protection.build_payload(existing)
        gh.update_branch_protection(user_token, owner, repo_name, default_branch, payload)
        refreshed = gh.branch_protection(user_token, owner, repo_name, default_branch)
        identity = github_app.load_identity()
        if branch_protection.app_bypasses_reviews(refreshed, identity.app_slug):
            print("PR mode disabled: the devbox GitHub App is allowed to bypass pull request review requirements.")
            return "unavailable"
        if branch_protection.has_required_review(refreshed):
            return "enforced"
        print("PR mode disabled: GitHub accepted branch protection but required reviews are not enforced.")
        return "unavailable"
    except GitHubApiError as exc:
        if exc.status_code in {403, 404, 422}:
            print(f"PR mode disabled: branch protection could not be enforced ({exc}).")
            return "unavailable"
        raise
