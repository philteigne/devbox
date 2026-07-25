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
DERIVED_IMAGE_SCHEMA = 8
NVM_VERSION = "v0.40.6"


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
    install_codex = launch.tools.get("codex") is True
    install_opencode = launch.tools.get("opencode") is True
    install_claude = launch.tools.get("claude") is True
    install_agy = launch.tools.get("agy") is True
    install_fvm = launch.tools.get("fvm") is True
    configured_node = launch.tools.get("node")
    if isinstance(configured_node, str):
        node_versions = (configured_node,)
    elif isinstance(configured_node, tuple):
        node_versions = configured_node
    else:
        node_versions = ()
    configured_bun = launch.tools.get("bun")
    bun_version = configured_bun if isinstance(configured_bun, str) else None
    if (
        not launch.apt
        and not node_versions
        and not bun_version
        and not install_codex
        and not install_opencode
        and not install_claude
        and not install_agy
        and not install_fvm
    ):
        return IMAGE_NAME, base_image_id

    digest = hashlib.sha256(
        json.dumps(
            {
                "base_image_id": base_image_id,
                "builder_schema": DERIVED_IMAGE_SCHEMA,
                "launch_config_hash": launch.normalized_hash(),
                "nvm_version": NVM_VERSION,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:16]
    image_name = f"devbox-{sanitize(owner)}-{sanitize(repo)}-runtime-{digest}"
    build_dir = run_state_dir(owner, repo) / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    dockerfile = build_dir / "Dockerfile"
    dockerfile.write_text(
        _derived_dockerfile(
            launch.apt,
            node_versions,
            bun_version,
            install_codex,
            install_opencode,
            install_claude,
            install_agy,
            install_fvm,
        ),
        encoding="utf-8",
        newline="\n",
    )

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


def _derived_dockerfile(
    packages: tuple[str, ...],
    node_versions: tuple[str, ...] = (),
    bun_version: str | None = None,
    install_codex: bool = False,
    install_opencode: bool = False,
    install_claude: bool = False,
    install_agy: bool = False,
    install_fvm: bool = False,
) -> str:
    lines: list[str] = [f"FROM {IMAGE_NAME}", ""]
    if node_versions:
        lines.extend(
            [
                "ENV NVM_DIR=/opt/devbox/nvm",
                "ENV BASH_ENV=/etc/devbox/bash-env",
                'ENV PATH="/opt/devbox/nvm/current/bin:${PATH}"',
                "",
            ]
        )
    if bun_version:
        lines.extend(
            [
                "ENV BUN_INSTALL=/opt/devbox/bun",
                'ENV PATH="/opt/devbox/bun/bin:${PATH}"',
                "",
            ]
        )
    if install_fvm:
        lines.extend(
            [
                "ENV FVM_INSTALL_DIR=/opt/devbox/fvm",
                "ENV FVM_CACHE_PATH=/devbox-home/.cache/fvm",
                'ENV PATH="/opt/devbox/fvm/bin:${PATH}"',
                "",
            ]
        )
    if (
        node_versions
        or bun_version
        or install_codex
        or install_opencode
        or install_claude
        or install_agy
        or install_fvm
    ):
        lines.extend(
            [
                'SHELL ["/bin/bash", "-o", "pipefail", "-c"]',
                "",
            ]
        )
    if node_versions:
        lines.extend(_nvm_install_lines(node_versions))
    if bun_version:
        lines.extend(_bun_install_lines(bun_version))
    if install_codex:
        lines.extend(_codex_install_lines())
    if install_opencode:
        lines.extend(_opencode_install_lines())
    if install_claude:
        lines.extend(_claude_install_lines())
    if install_agy:
        lines.extend(_agy_install_lines())
    if install_fvm:
        lines.extend(_fvm_install_lines())
    if packages:
        package_lines = " \\\n".join(f"        {package}" for package in packages)
        lines.extend(
            [
                "RUN apt-get update \\",
                "    && apt-get install -y --no-install-recommends \\",
                f"{package_lines} \\",
                "    && apt-get clean \\",
                "    && rm -rf /var/lib/apt/lists/*",
                "",
            ]
        )
    return "\n".join(lines)


def _nvm_install_lines(node_versions: tuple[str, ...]) -> list[str]:
    default_version = node_versions[0]
    install_lines = [f'    && nvm install "{version}" \\' for version in node_versions]
    return [
        'RUN mkdir -p "$NVM_DIR" /etc/devbox \\',
        f"    && curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/{NVM_VERSION}/install.sh \\",
        '        | PROFILE=/dev/null NVM_DIR="$NVM_DIR" bash \\',
        '    && . "$NVM_DIR/nvm.sh" \\',
        *install_lines,
        f'    && nvm alias default "{default_version}" \\',
        "    && nvm use default \\",
        '    && node_root="$(dirname "$(dirname "$(nvm which default)")")" \\',
        '    && ln -sfn "$node_root" "$NVM_DIR/current" \\',
        "    && printf '%s\\n' \\",
        "        'export NVM_DIR=/opt/devbox/nvm' \\",
        "        '[ -s \"$NVM_DIR/nvm.sh\" ] && . \"$NVM_DIR/nvm.sh\"' \\",
        "        > /etc/devbox/nvm-init.sh \\",
        "    && ln -sf /etc/devbox/nvm-init.sh /etc/profile.d/devbox-nvm.sh \\",
        "    && ln -sf /etc/devbox/nvm-init.sh /etc/devbox/bash-env \\",
        "    && printf '%s\\n' '[ -r /etc/devbox/nvm-init.sh ] && . /etc/devbox/nvm-init.sh' \\",
        "        >> /etc/bash.bashrc \\",
        '    && chmod -R a+rwX "$NVM_DIR"',
        "",
    ]


def _bun_install_lines(version: str) -> list[str]:
    return [
        "RUN apt-get update \\",
        "    && apt-get install -y --no-install-recommends unzip \\",
        "    && rm -rf /var/lib/apt/lists/* \\",
        '    && mkdir -p "$BUN_INSTALL" \\',
        "    && curl -fsSL https://bun.com/install \\",
        f'        | HOME=/root BUN_INSTALL="$BUN_INSTALL" bash -s -- "bun-v{version}" \\',
        '    && chmod -R a+rwX "$BUN_INSTALL" \\',
        f'    && test "$(bun --version)" = "{version}"',
        "",
    ]


def _opencode_install_lines() -> list[str]:
    return [
        "RUN curl -fsSL https://opencode.ai/install \\",
        "        | HOME=/root SHELL=/bin/bash bash -s -- --no-modify-path \\",
        "    && install -m 0755 /root/.opencode/bin/opencode /usr/local/bin/opencode \\",
        "    && rm -rf /root/.opencode \\",
        "    && opencode --version",
        "",
    ]


def _codex_install_lines() -> list[str]:
    return [
        "RUN mkdir -p /opt/devbox/codex-cli \\",
        "    && curl -fsSL https://chatgpt.com/codex/install.sh \\",
        "        | HOME=/root \\",
        "          CODEX_HOME=/opt/devbox/codex-cli \\",
        "          CODEX_INSTALL_DIR=/usr/local/bin \\",
        "          CODEX_NON_INTERACTIVE=1 sh \\",
        "    && chmod -R a+rX /opt/devbox/codex-cli \\",
        "    && codex --version",
        "",
    ]


def _claude_install_lines() -> list[str]:
    return [
        "RUN install -d -m 0755 /etc/apt/keyrings \\",
        "    && curl -fsSL https://downloads.claude.ai/keys/claude-code.asc \\",
        "        -o /etc/apt/keyrings/claude-code.asc \\",
        '    && echo "deb [signed-by=/etc/apt/keyrings/claude-code.asc] \\',
        '        https://downloads.claude.ai/claude-code/apt/stable stable main" \\',
        "        > /etc/apt/sources.list.d/claude-code.list \\",
        "    && apt-get update \\",
        "    && apt-get install -y --no-install-recommends claude-code \\",
        "    && rm -rf /var/lib/apt/lists/* \\",
        "    && claude --version",
        "",
    ]


def _agy_install_lines() -> list[str]:
    return [
        "RUN mkdir -p /opt/devbox/agy/bin \\",
        "    && curl -fsSL https://antigravity.google/cli/install.sh \\",
        "        | HOME=/root bash -s -- --dir /opt/devbox/agy/bin \\",
        "    && ln -s /opt/devbox/agy/bin/agy /usr/local/bin/agy \\",
        "    && chmod -R a+rwX /opt/devbox/agy \\",
        "    && rm -rf /root/.cache/antigravity \\",
        "    && agy --version",
        "",
    ]


def _fvm_install_lines() -> list[str]:
    return [
        'RUN mkdir -p "$FVM_INSTALL_DIR" \\',
        "    && curl -fsSL https://fvm.app/install.sh \\",
        '        | HOME=/opt/devbox FVM_INSTALL_DIR="$FVM_INSTALL_DIR" CI=1 bash \\',
        '    && chmod -R a+rwX "$FVM_INSTALL_DIR" \\',
        "    && fvm --version",
        "",
    ]


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
    home_path: Path,
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
        "home_path": str(home_path.resolve()),
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
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(value + "\n")


def create_container(
    *,
    name: str,
    label_digest: str,
    repo_root: Path,
    home_path: Path,
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
        "-v",
        f"{home_path}:/devbox-home",
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
