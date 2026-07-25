from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ..core import branch_protection, config, docker, gh, github_app, launch_config, paths
from ..core.envfile import read_env
from ..core.errors import DevboxError
from ..core.gitctx import git_autocrlf, resolve_repo
from ..core.token_file import write_atomic


def run(path: str = ".", *, no_pr: bool = False) -> int:
    ctx = resolve_repo(path)
    _containment_guard(paths.devbox_home(), ctx.root)
    launch = launch_config.load(ctx.owner, ctx.repo)
    host_git_autocrlf = git_autocrlf(ctx.root)
    docker.docker_info()

    project = config.read_project_config(ctx.owner, ctx.repo)
    mode = "NO-PR"
    token_data: dict | None = None
    identity = None
    default_branch = project.default_branch if project else "main"
    repo_id = project.repo_id if project else _fallback_repo_id(ctx.owner, ctx.repo)

    if no_pr:
        print("NO-PR mode selected by --no-pr.")
    elif project and project.pr_eligible:
        identity = github_app.load_identity()
        token_data = _live_pr_mode_check(identity, project)
        if token_data:
            mode = "PR"
        else:
            print("Safety check failed; downgrading to NO-PR mode.")
    elif project:
        if project.branch_protection != "enforced":
            print("PR mode disabled for safety: cached config says branch protection is unavailable.")
        if project.app_repo_access != "granted":
            print("PR mode disabled: cached config says the App installation does not include this repo.")
    else:
        print(f"devbox not initialized for `{ctx.owner}/{ctx.repo}`; using NO-PR mode.")

    base_image_id = docker.ensure_base_image()
    image_name, selected_image_id = docker.ensure_launch_image(
        ctx.owner,
        ctx.repo,
        launch,
        base_image_id,
    )
    spec = docker.container_spec(ctx.owner, ctx.repo, repo_id)
    state_dir = docker.run_state_dir(ctx.owner, ctx.repo)
    home_path = state_dir / "home"
    home_path.mkdir(parents=True, exist_ok=True)
    run_mount = state_dir if mode == "PR" else None

    env = _container_env(
        project,
        ctx.owner,
        ctx.repo,
        default_branch,
        mode,
        launch.env,
        git_autocrlf=host_git_autocrlf,
    )
    should_start_refresher = mode == "PR" and token_data is not None and identity is not None and project is not None
    if should_start_refresher:
        write_atomic(state_dir / "token", token_data["token"])

    fingerprint = docker.fingerprint(
        image=selected_image_id,
        mode=mode,
        repo_root=ctx.root,
        default_branch=default_branch,
        home_path=home_path,
        run_path=run_mount,
        ai_env_path=paths.secrets_dir() / "ai.env",
        launch_config_hash=launch.normalized_hash(),
        ports=list(launch.ports),
        command=list(launch.command),
        launch_source_hash=launch.source_hash,
        git_autocrlf=host_git_autocrlf,
    )
    existing = docker.inspect_container(spec.name)
    cached_fingerprint = docker.read_fingerprint(state_dir)
    if existing and cached_fingerprint == fingerprint:
        if docker.is_running(existing):
            print(f"Reusing running container `{spec.name}`.")
        else:
            docker.start_container(spec.name)
            print(f"Started existing container `{spec.name}`.")
    else:
        if existing:
            docker.remove_container(spec.name)
            print(f"Removed stale container `{spec.name}`.")
        docker.create_container(
            name=spec.name,
            label_digest=spec.label_digest,
            repo_root=ctx.root,
            home_path=home_path,
            mode=mode,
            env=env,
            run_path=run_mount,
            image_name=image_name,
            ports=list(launch.ports),
            command=list(launch.command),
        )
        docker.write_fingerprint(state_dir, fingerprint)
        print(f"Created container `{spec.name}`.")

    if should_start_refresher:
        _start_refresher(project, spec.name, state_dir)

    print(f"Mode: {mode}")
    print("Your repo is mounted at /workspace inside the container.")
    uri = _attach_folder_uri(spec.name)
    print(f'Open in Cursor:  cursor --folder-uri "{uri}"')
    print(f'Open in VSCode:  code --folder-uri "{uri}"')
    print(f"Shell:           docker exec -it {spec.name} bash   # starts in /workspace")
    if mode == "PR":
        print("PR capability: agent can push feature branches and open pull requests.")
    else:
        print("PR capability: disabled; work is local only and you push from the host.")
    return 0


def _attach_folder_uri(container_name: str, folder: str = "/workspace") -> str:
    # Cursor/VSCode open a running container + folder via a hex-encoded name.
    hex_name = container_name.encode("utf-8").hex()
    return f"vscode-remote://attached-container+{hex_name}{folder}"


def _containment_guard(devbox_home: Path, repo_root: Path) -> None:
    home = devbox_home.resolve()
    repo = repo_root.resolve()
    if home == repo or _is_relative_to(home, repo) or _is_relative_to(repo, home):
        raise DevboxError(
            f"Refusing to start: devbox home `{home}` and target repo `{repo}` overlap. "
            "Move devbox outside the target repository."
        )


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _fallback_repo_id(owner: str, repo: str) -> str:
    import hashlib

    return hashlib.sha256(f"{owner}/{repo}".encode("utf-8")).hexdigest()[:12]


def _live_pr_mode_check(identity: github_app.AppIdentity, project: config.ProjectConfig) -> dict | None:
    user_token = gh.require_gh_auth()
    try:
        protection = gh.branch_protection(user_token, project.owner, project.repo, project.default_branch)
    except Exception as exc:
        print(f"Could not verify live branch protection: {exc}")
        config.refresh_cached_safety(project.owner, project.repo, branch_protection="unavailable")
        return None
    if branch_protection.app_bypasses_reviews(protection, identity.app_slug):
        print("Live branch protection lets the devbox GitHub App bypass pull request review requirements.")
        config.refresh_cached_safety(project.owner, project.repo, branch_protection="unavailable")
        return None
    if not branch_protection.has_required_review(protection):
        config.refresh_cached_safety(project.owner, project.repo, branch_protection="unavailable")
        return None
    try:
        token_data = github_app.create_installation_token(identity, project.installation_id, project.repo)
    except Exception as exc:
        print(f"Could not mint a repo-scoped installation token: {exc}")
        config.refresh_cached_safety(project.owner, project.repo, app_repo_access="missing")
        return None
    return token_data


def _container_env(
    project: config.ProjectConfig | None,
    owner: str,
    repo: str,
    default_branch: str,
    mode: str,
    launch_env: dict[str, str],
    *,
    git_autocrlf: str | None = None,
) -> dict[str, str]:
    ai_env = read_env(paths.secrets_dir() / "ai.env")
    env = {
        "GIT_USER_NAME": "devbox-local" if mode == "NO-PR" else (project and _project_git_name()) or "devbox[bot]",
        "GIT_USER_EMAIL": "devbox-local@example.invalid" if mode == "NO-PR" else (project and _project_git_email()) or "devbox@example.invalid",
        "DEFAULT_BRANCH": default_branch,
        "OWNER": owner,
        "REPO": repo,
        "MODE": mode,
    }
    env.update(launch_env)
    for key, value in ai_env.items():
        if value:
            env[key] = value
    env["HOME"] = "/devbox-home"
    if git_autocrlf:
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "core.autocrlf"
        env["GIT_CONFIG_VALUE_0"] = git_autocrlf
    return env


def _project_git_name() -> str:
    try:
        return github_app.load_identity().git_user_name or "devbox[bot]"
    except DevboxError:
        return "devbox[bot]"


def _project_git_email() -> str:
    try:
        return github_app.load_identity().git_user_email or "devbox@example.invalid"
    except DevboxError:
        return "devbox@example.invalid"


def _start_refresher(project: config.ProjectConfig, container_name: str, state_dir: Path) -> None:
    pid_file = state_dir / "refresher.pid"
    if _pid_running(pid_file):
        return
    cmd = [
        sys.executable,
        "-m",
        "devbox.core.refresher",
        "--installation-id",
        project.installation_id,
        "--repo",
        project.repo,
        "--container",
        container_name,
        "--token-file",
        str(state_dir / "token"),
        "--pid-file",
        str(pid_file),
    ]
    kwargs = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
        "cwd": str(paths.devbox_home()),
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    subprocess.Popen(cmd, **kwargs)


def _pid_running(pid_file: Path) -> bool:
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
