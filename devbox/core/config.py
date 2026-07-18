from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import paths
from .envfile import read_env, write_env


CONFIG_KEYS = [
    "OWNER",
    "REPO",
    "REPO_ID",
    "DEFAULT_BRANCH",
    "INSTALLATION_ID",
    "BRANCH_PROTECTION",
    "APP_REPO_ACCESS",
]


@dataclass
class ProjectConfig:
    owner: str
    repo: str
    repo_id: str
    default_branch: str
    installation_id: str
    branch_protection: str
    app_repo_access: str

    @property
    def pr_eligible(self) -> bool:
        return self.branch_protection == "enforced" and self.app_repo_access == "granted"

    def to_env(self) -> dict[str, str]:
        return {
            "OWNER": self.owner,
            "REPO": self.repo,
            "REPO_ID": self.repo_id,
            "DEFAULT_BRANCH": self.default_branch,
            "INSTALLATION_ID": self.installation_id,
            "BRANCH_PROTECTION": self.branch_protection,
            "APP_REPO_ACCESS": self.app_repo_access,
        }


def path_for(owner: str, repo: str) -> Path:
    return paths.config_dir() / owner / repo / "config.env"


def read_project_config(owner: str, repo: str) -> ProjectConfig | None:
    path = path_for(owner, repo)
    if not path.exists():
        return None
    data = read_env(path)
    if not all(data.get(key) for key in CONFIG_KEYS):
        return None
    if data["OWNER"] != owner or data["REPO"] != repo:
        return None
    if data["BRANCH_PROTECTION"] not in {"enforced", "unavailable"}:
        return None
    if data["APP_REPO_ACCESS"] not in {"granted", "missing"}:
        return None
    return ProjectConfig(
        owner=data["OWNER"],
        repo=data["REPO"],
        repo_id=data["REPO_ID"],
        default_branch=data["DEFAULT_BRANCH"],
        installation_id=data["INSTALLATION_ID"],
        branch_protection=data["BRANCH_PROTECTION"],
        app_repo_access=data["APP_REPO_ACCESS"],
    )


def write_project_config(config: ProjectConfig) -> Path:
    path = path_for(config.owner, config.repo)
    write_env(path, config.to_env(), CONFIG_KEYS)
    return path


def mark_app_repo_missing(owner: str, repo: str) -> None:
    existing = read_project_config(owner, repo)
    if existing is None:
        return
    existing.app_repo_access = "missing"
    write_project_config(existing)


def refresh_cached_safety(owner: str, repo: str, *, branch_protection: str | None = None, app_repo_access: str | None = None) -> None:
    existing = read_project_config(owner, repo)
    if existing is None:
        return
    if branch_protection is not None:
        existing.branch_protection = branch_protection
    if app_repo_access is not None:
        existing.app_repo_access = app_repo_access
    write_project_config(existing)
