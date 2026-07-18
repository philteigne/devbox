from __future__ import annotations

import json
import os
import shutil
from typing import Any

from . import paths
from .envfile import read_env
from .errors import DevboxError, GitHubApiError
from .proc import run


API_ROOT = "https://api.github.com"
API_VERSION = "2022-11-28"


def require_gh_auth() -> str:
    if shutil.which("gh") is None:
        raise DevboxError("GitHub CLI (`gh`) is not installed or is not on PATH.")
    status = run(["gh", "auth", "status"], check=False)
    if status.returncode != 0:
        raise DevboxError("Not logged in to GitHub. Run `gh auth login` first.")
    token = run(["gh", "auth", "token"]).stdout.strip()
    if not token:
        raise DevboxError("GitHub CLI did not return an auth token. Run `gh auth login` first.")
    return token


def classic_pat() -> str | None:
    if os.environ.get("DEVBOX_PAT"):
        return os.environ["DEVBOX_PAT"]
    env_path = paths.secrets_dir() / "gh.env"
    env = read_env(env_path)
    return env.get("DEVBOX_PAT") or env.get("GITHUB_TOKEN") or env.get("GH_TOKEN")


def user(token: str) -> dict[str, Any]:
    return api_json("GET", "/user", token=token)


def repo(token: str, owner: str, repo_name: str) -> dict[str, Any]:
    return api_json("GET", f"/repos/{owner}/{repo_name}", token=token)


def branch_protection(token: str, owner: str, repo_name: str, branch: str) -> dict[str, Any] | None:
    try:
        return api_json("GET", f"/repos/{owner}/{repo_name}/branches/{branch}/protection", token=token)
    except GitHubApiError as exc:
        if exc.status_code == 404:
            return None
        raise


def update_branch_protection(
    token: str,
    owner: str,
    repo_name: str,
    branch: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return api_json(
        "PUT",
        f"/repos/{owner}/{repo_name}/branches/{branch}/protection",
        token=token,
        body=payload,
    )


def add_repo_to_installation(token: str, installation_id: str, repo_id: str) -> None:
    api_json(
        "PUT",
        f"/user/installations/{installation_id}/repositories/{repo_id}",
        token=token,
        body=None,
        expected=(204,),
    )


def api_json(
    method: str,
    path: str,
    *,
    token: str,
    body: dict[str, Any] | None = None,
    expected: tuple[int, ...] = (200, 201, 204),
) -> Any:
    try:
        import requests
    except ImportError as exc:
        raise DevboxError("requests is not installed. Run `pip install -r requirements.txt`.") from exc

    response = requests.request(
        method,
        f"{API_ROOT}{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": API_VERSION,
        },
        json=body,
        timeout=30,
    )
    if response.status_code not in expected:
        raise GitHubApiError(_format_error(response), status_code=response.status_code)
    if response.status_code == 204 or not response.content:
        return None
    return response.json()


def _format_error(response: Any) -> str:
    try:
        payload = response.json()
        message = payload.get("message") or json.dumps(payload)
    except ValueError:
        message = response.text.strip()
    return f"GitHub API {response.request.method} {response.request.path_url} failed ({response.status_code}): {message}"
