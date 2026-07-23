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
from .launch_config import LaunchConfig
from .proc import run


IMAGE_NAME = "devbox-base"


@dataclass(frozen=True)
class ContainerSpec:
    name: str
    state_key: str
    label_digest: str


def docker_info() -> None:
    run(["docker", "info"])


RUNTIME_LABEL = "devbox.runtime"


def image_exists(image_name: str = IMAGE_NAME) -> bool:
    proc = run(["docker", "image", "inspect", image_name], check=False)
    return proc.returncode == 0


def runtime_hash() -> str:
    runtime = paths.runtime_dir()
    digest = hashlib.sha256()
    for file in sorted(runtime.rglob("*")):
        if file.is_file():
            digest.update(file.relative_to(runtime).as_posix().encode("utf-8"))
            digest.update(file_hash(file).encode("utf-8"))
    return digest.hexdigest()[:16]


def _image_runtime_label() -> str:
    proc = run(
        ["docker", "image", "inspect", IMAGE_NAME, "--format", f'{{{{index .Config.Labels "{RUNTIME_LABEL}"}}}}'],
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def ensure_base_image() -> str:
    current = runtime_hash()
    if image_exists() and _image_runtime_label() == current:
        return image_id(IMAGE_NAME)

    reason = "first run" if not image_exists() else "runtime files changed"
    print(
        f"Building base image `{IMAGE_NAME}` ({reason}; this can take a few minutes)...",
        flush=True,
    )
    run(
        [
            "docker",
            "build",
            "--label",
            f"{RUNTIME_LABEL}={current}",
            "-t",
            IMAGE_NAME,
            str(paths.runtime_dir()),
        ],
        stream=True,
    )
    print(f"Base image `{IMAGE_NAME}` built.", flush=True)
    return image_id(IMAGE_NAME)


def image_id(image_name: str = IMAGE_NAME) -> str:
    return run(["docker", "image", "inspect", image_name, "--format", "{{.Id}}"]).stdout.strip()


def ensure_launch_image(
    owner: str,
    repo: str,
    launch: LaunchConfig,
    base_image_id: str,
) -> tuple[str, str]:
    if not launch.apt:
        return IMAGE_NAME, base_image_id

    digest = hashlib.sha256(
        json.dumps(
            {
                "base_image_id": base_image_id,
                "launch_config_hash": launch.normalized_hash(),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:16]
    image_name = f"devbox-{sanitize(owner)}-{sanitize(repo)}-runtime-{digest}"
    build_dir = run_state_dir(owner, repo) / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    dockerfile = build_dir / "Dockerfile"
    dockerfile.write_text(_derived_dockerfile(launch.apt), encoding="utf-8", newline="\n")

    if not image_exists(image_name):
        print(
            f"Building launch image `{image_name}` (this can take a few minutes)...",
            flush=True,
        )
        run(
            ["docker", "build", "-t", image_name, str(build_dir)],
            stream=True,
        )
        print(f"Launch image `{image_name}` built.", flush=True)
    return image_name, image_id(image_name)


def _derived_dockerfile(packages: tuple[str, ...]) -> str:
    package_lines = " \\\n".join(f"        {package}" for package in packages)
    return (
        f"FROM {IMAGE_NAME}\n"
        "\n"
        "RUN apt-get update \\\n"
        "    && apt-get install -y --no-install-recommends \\\n"
        f"{package_lines} \\\n"
        "    && apt-get clean \\\n"
        "    && rm -rf /var/lib/apt/lists/*\n"
    )


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
    launch_config_hash: str,
    ports: list[int],
    command: list[str],
    launch_source_hash: str,
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
        "launch_config_hash": launch_config_hash,
        "ports": ports,
        "command": command,
        "launch_source_hash": launch_source_hash,
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
    image_name: str,
    ports: list[int],
    command: list[str],
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
    for port in ports:
        args.extend(["-p", f"{port}:{port}"])
    args.append(image_name)
    args.extend(command)
    run(args)
