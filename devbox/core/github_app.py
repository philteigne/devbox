from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import gh, paths
from .envfile import read_env, write_env
from .errors import DevboxError, GitHubApiError


APP_ENV_KEYS = ["APP_ID", "CLIENT_ID", "APP_SLUG", "GIT_USER_NAME", "GIT_USER_EMAIL"]
DEFAULT_TOKEN_PERMISSIONS = {
    "contents": "write",
    "pull_requests": "write",
}


@dataclass
class AppIdentity:
    app_id: str
    client_id: str
    app_slug: str
    git_user_name: str
    git_user_email: str
    private_key_path: Path


def load_identity() -> AppIdentity:
    app_path = paths.app_dir()
    env_path = app_path / "app.env"
    if not env_path.exists():
        raise DevboxError("App identity is missing. Expected app/app.env.")
    env = read_env(env_path)
    app_id = env.get("APP_ID", "").strip()
    if not app_id:
        raise DevboxError("APP_ID is missing from app/app.env.")
    keys = sorted(app_path.glob("*.pem"))
    if len(keys) != 1:
        raise DevboxError("App identity requires exactly one private key PEM in app/.")
    return AppIdentity(
        app_id=app_id,
        client_id=env.get("CLIENT_ID", "").strip(),
        app_slug=env.get("APP_SLUG", "").strip(),
        git_user_name=env.get("GIT_USER_NAME", "").strip(),
        git_user_email=env.get("GIT_USER_EMAIL", "").strip(),
        private_key_path=keys[0],
    )


def save_derived_identity(identity: AppIdentity, slug: str) -> AppIdentity:
    env_path = paths.app_dir() / "app.env"
    current = read_env(env_path)
    current.setdefault("APP_ID", identity.app_id)
    current.setdefault("CLIENT_ID", identity.client_id)
    if not current.get("APP_SLUG"):
        current["APP_SLUG"] = slug
    if not current.get("GIT_USER_NAME"):
        current["GIT_USER_NAME"] = f"{slug}[bot]"
    if not current.get("GIT_USER_EMAIL"):
        current["GIT_USER_EMAIL"] = f"{identity.app_id}+{slug}[bot]@users.noreply.github.com"
    write_env(env_path, current, APP_ENV_KEYS)
    return AppIdentity(
        app_id=current.get("APP_ID", ""),
        client_id=current.get("CLIENT_ID", ""),
        app_slug=current.get("APP_SLUG", ""),
        git_user_name=current.get("GIT_USER_NAME", ""),
        git_user_email=current.get("GIT_USER_EMAIL", ""),
        private_key_path=identity.private_key_path,
    )


def jwt_token(identity: AppIdentity) -> str:
    try:
        import jwt
    except ImportError as exc:
        raise DevboxError("PyJWT is not installed. Run `pip install -r requirements.txt`.") from exc
    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + 9 * 60,
        "iss": identity.app_id,
    }
    return jwt.encode(payload, identity.private_key_path.read_text(encoding="utf-8"), algorithm="RS256")


def app_request(identity: AppIdentity, method: str, path: str, body: dict[str, Any] | None = None, expected: tuple[int, ...] = (200, 201, 204)) -> Any:
    try:
        import requests
    except ImportError as exc:
        raise DevboxError("requests is not installed. Run `pip install -r requirements.txt`.") from exc

    token = jwt_token(identity)
    response = requests.request(
        method,
        f"{gh.API_ROOT}{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": gh.API_VERSION,
        },
        json=body,
        timeout=30,
    )
    if response.status_code not in expected:
        raise GitHubApiError(_format_error(response), status_code=response.status_code)
    if response.status_code == 204 or not response.content:
        return None
    return response.json()


def get_app(identity: AppIdentity) -> dict[str, Any]:
    return app_request(identity, "GET", "/app")


def installations(identity: AppIdentity) -> list[dict[str, Any]]:
    return app_request(identity, "GET", "/app/installations")


def create_installation_token(
    identity: AppIdentity,
    installation_id: str,
    repo_name: str,
    *,
    permissions: dict[str, str] | None = DEFAULT_TOKEN_PERMISSIONS,
) -> dict[str, Any]:
    body: dict[str, Any] = {"repositories": [repo_name]}
    if permissions:
        body["permissions"] = permissions
    return app_request(identity, "POST", f"/app/installations/{installation_id}/access_tokens", body=body)


def verify_repo_access(identity: AppIdentity, installation_id: str, repo_name: str) -> bool:
    try:
        create_installation_token(identity, installation_id, repo_name)
        return True
    except GitHubApiError:
        return False


def _format_error(response: Any) -> str:
    try:
        message = response.json().get("message", response.text)
    except ValueError:
        message = response.text.strip()
    return f"GitHub App API {response.request.method} {response.request.path_url} failed ({response.status_code}): {message}"
