from __future__ import annotations

import hashlib
import json
import os
import platform
import re
from dataclasses import dataclass
from pathlib import Path

from . import paths
from .errors import DevboxError
from .proc import run


IMAGE_NAME = "devbox-base"


@dataclass(frozen=True)
class ContainerSpec:
    name: str
    state_key: str
    label_digest: str


def docker_info() -> None:
    run(["docker", "info"])


def image_exists() -> bool:
    proc = run(["docker", "image", "inspect", IMAGE_NAME], check=False)
    return proc.returncode == 0


def ensure_base_image() -> str:
    if not image_exists():
        run(["docker", "build", "-t", IMAGE_NAME, str(paths.runtime_dir())])
    return image_id()


def image_id() -> str:
    return run(["docker", "image", "inspect", IMAGE_NAME, "--format", "{{.Id}}"]).stdout.strip()


def container_spec(owner: str, repo: str, repo_id: str) -> ContainerSpec:
    base = sanitize(f"{owner}-{repo}")
    shortid = sanitize(str(repo_id or ""))[:12] or hashlib.sha256(f"{owner}/{repo}".encode()).hexdigest()[:12]
    name = f"devbox-{base}-{shortid}"[:128].rstrip("-")
    state_key = sanitize(f"{owner}-{repo}")
    label_digest = hashlib.sha256(f"{owner}/{repo}/{repo_id}".encode("utf-8")).hexdigest()[:16]
    return ContainerSpec(name=name, state_key=state_key, label_digest=label_digest)


def sanitize(value: str) -> str:
    lowered = value.lower()
    normalized = re.sub(r"[^a-z0-9_.-]+", "-", lowered)
    normalized = normalized.replace("_", "-").replace(".", "-")
    normalized = re.sub(r"-+", "-", normalized).strip("-")
    return normalized or "repo"


def inspect_container(name: str) -> dict | None:
    proc = run(["docker", "inspect", name], check=False)
    if proc.returncode != 0:
        return None
    data = json.loads(proc.stdout)
    return data[0] if data else None


def is_running(container: dict) -> bool:
    return bool((container.get("State") or {}).get("Running"))


def start_container(name: str) -> None:
    run(["docker", "start", name])


def remove_container(name: str) -> None:
    run(["docker", "rm", "-f", name])


def fingerprint(
    *,
    image: str,
    mode: str,
    repo_root: Path,
    default_branch: str,
    run_path: Path | None,
    ai_env_path: Path,
) -> str:
    payload = {
        "image": image,
        "mode": mode,
        "repo_root": str(repo_root.resolve()),
        "default_branch": default_branch,
        "run_path": str(run_path.resolve()) if run_path else "",
        "entrypoint": file_hash(paths.runtime_dir() / "entrypoint.sh"),
        "credential_helper": file_hash(paths.runtime_dir() / "git-credential-devbox.sh"),
        "gh_wrapper": file_hash(paths.runtime_dir() / "gh-wrapper.sh"),
        "ai_env_hash": file_hash(ai_env_path),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_state_dir(owner: str, repo: str) -> Path:
    state = paths.run_dir() / sanitize(f"{owner}-{repo}")
    state.mkdir(parents=True, exist_ok=True)
    return state


def read_fingerprint(state_dir: Path) -> str | None:
    path = state_dir / "fingerprint"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8").strip()


def write_fingerprint(state_dir: Path, value: str) -> None:
    path = state_dir / "fingerprint"
    path.write_text(value + "\n", encoding="utf-8", newline="\n")


def create_container(
    *,
    name: str,
    label_digest: str,
    repo_root: Path,
    mode: str,
    env: dict[str, str],
    run_path: Path | None,
) -> None:
    args = [
        "docker",
        "run",
        "-d",
        "--name",
        name,
        "--label",
        "devbox.managed=true",
        "--label",
        f"devbox.digest={label_digest}",
        "-v",
        f"{repo_root}:/workspace",
        "-w",
        "/workspace",
    ]
    if run_path is not None:
        args.extend(["-v", f"{run_path}:/devbox-run"])
    if platform.system().lower() == "linux":
        args.extend(["--user", f"{os.getuid()}:{os.getgid()}"])
    for key, value in env.items():
        args.extend(["-e", f"{key}={value}"])
    args.extend([IMAGE_NAME, "sleep", "infinity"])
    run(args)

