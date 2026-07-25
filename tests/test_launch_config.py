from __future__ import annotations

import hashlib
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from devbox.commands import start
from devbox.core import docker, launch_config
from devbox.core.errors import DevboxError
from devbox.core.gitctx import RepoContext


class LaunchConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path.cwd() / ".test-tmp" / "launch-config" / self._testMethodName
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        patcher = patch.object(
            launch_config.paths,
            "config_dir",
            return_value=self.temp_dir / "config",
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_launch(self, body: str) -> Path:
        path = launch_config.path_for("owner", "repo")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8", newline="\n")
        return path

    def assert_invalid(self, body: str, message: str) -> None:
        self.write_launch(body)
        with self.assertRaisesRegex(DevboxError, message):
            launch_config.load("owner", "repo")

    def test_missing_config_uses_defaults(self) -> None:
        config = launch_config.load("owner", "repo")
        self.assertEqual(config, launch_config.default_config())
        self.assertEqual(
            config.tools,
            {
                "codex": False,
                "opencode": False,
                "claude": False,
                "agy": False,
                "fvm": False,
            },
        )
        self.assertIsNone(config.path)
        self.assertEqual(config.source_hash, "")

    def test_valid_config_is_normalized(self) -> None:
        body = """\
version: 1
tools:
  codex: true
  opencode: false
  claude: true
  agy: false
  fvm: true
  node: 24
  bun: 1.3.9
apt:
  - ripgrep
  - jq
  - ripgrep
env:
  STRING: value
  INTEGER: 42
  FLOAT: 1.5
  BOOLEAN: true
  OMITTED: null
ports:
  - 3000
  - 5173
  - 3000
command:
  - python
  - -m
  - http.server
"""
        path = self.write_launch(body)

        config = launch_config.load("owner", "repo")

        self.assertEqual(
            config.tools,
            {
                "codex": True,
                "opencode": False,
                "claude": True,
                "agy": False,
                "fvm": True,
                "node": "24",
                "bun": "1.3.9",
            },
        )
        self.assertEqual(config.apt, ("ripgrep", "jq"))
        self.assertEqual(
            config.env,
            {
                "STRING": "value",
                "INTEGER": "42",
                "FLOAT": "1.5",
                "BOOLEAN": "True",
            },
        )
        self.assertEqual(config.ports, (3000, 5173))
        self.assertEqual(config.command, ("python", "-m", "http.server"))
        self.assertEqual(config.path, path)
        self.assertEqual(config.source_hash, hashlib.sha256(body.encode("utf-8")).hexdigest())
        self.assertEqual(config.normalized_hash(), launch_config.load("owner", "repo").normalized_hash())

    def test_unknown_top_level_key_is_rejected(self) -> None:
        self.assert_invalid(
            "version: 1\nmounts: []\n",
            r"unknown key `mounts`",
        )

    def test_invalid_package_names_are_rejected(self) -> None:
        self.assert_invalid(
            "version: 1\napt:\n  - curl && whoami\n",
            r"apt\[0\] contains invalid package name `curl && whoami`",
        )

    def test_invalid_node_versions_are_rejected(self) -> None:
        invalid_versions = ["true", "0", "latest", "24.1", '"24; RUN whoami"']
        for version in invalid_versions:
            with self.subTest(version=version):
                self.assert_invalid(
                    f"version: 1\ntools:\n  node: {version}\n",
                    r"tools\.node must be a positive major version or semantic version string",
                )

    def test_optional_agent_tools_must_be_boolean(self) -> None:
        for tool in ("codex", "opencode", "claude", "agy", "fvm"):
            with self.subTest(tool=tool):
                self.assert_invalid(
                    f'version: 1\ntools:\n  {tool}: "yes"\n',
                    rf"tools\.{tool} must be a boolean",
                )

    def test_node_accepts_major_or_exact_semantic_version(self) -> None:
        for configured, expected in [("22", "22"), ('"24.18.0"', "24.18.0")]:
            with self.subTest(configured=configured):
                self.write_launch(f"version: 1\ntools:\n  node: {configured}\n")
                config = launch_config.load("owner", "repo")
                self.assertEqual(config.tools["node"], expected)

    def test_node_accepts_ordered_version_list_and_removes_duplicates(self) -> None:
        self.write_launch(
            """\
version: 1
tools:
  node:
    - 22
    - 16
    - 22
"""
        )

        config = launch_config.load("owner", "repo")

        self.assertEqual(config.tools["node"], ("22", "16"))

    def test_node_version_list_must_be_non_empty_and_valid(self) -> None:
        self.assert_invalid(
            "version: 1\ntools:\n  node: []\n",
            r"tools\.node must be a positive version or a non-empty list of versions",
        )
        self.assert_invalid(
            "version: 1\ntools:\n  node:\n    - 22\n    - latest\n",
            r"tools\.node\[1\] must be a positive major version or semantic version string",
        )

    def test_bun_requires_an_exact_semantic_version(self) -> None:
        for configured in ("true", "1", "latest", "1.3", '"1.3.9; whoami"'):
            with self.subTest(configured=configured):
                self.assert_invalid(
                    f"version: 1\ntools:\n  bun: {configured}\n",
                    r"tools\.bun must be an exact semantic version string",
                )

    def test_invalid_env_keys_are_rejected(self) -> None:
        self.assert_invalid(
            "version: 1\nenv:\n  BAD-NAME: value\n",
            r"env contains invalid variable name `BAD-NAME`",
        )

    def test_invalid_ports_are_rejected(self) -> None:
        invalid_ports = ["0", "65536", "true", '"3000"']
        for port in invalid_ports:
            with self.subTest(port=port):
                self.assert_invalid(
                    f"version: 1\nports:\n  - {port}\n",
                    r"ports\[0\] must be an integer from 1 to 65535",
                )

    def test_invalid_command_values_are_rejected(self) -> None:
        invalid_commands = ["[]", "sleep", "[sleep, 1]", '[sleep, ""]']
        for command in invalid_commands:
            with self.subTest(command=command):
                self.assert_invalid(
                    f"version: 1\ncommand: {command}\n",
                    r"command must be a non-empty list of non-empty strings",
                )


class DockerLaunchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path.cwd() / ".test-tmp" / "docker-launch" / self._testMethodName
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def test_base_runtime_does_not_install_optional_tools(self) -> None:
        dockerfile = (docker.paths.runtime_dir() / "Dockerfile").read_text(encoding="utf-8")
        self.assertNotIn("opencode.ai/install", dockerfile)
        self.assertNotIn("downloads.claude.ai", dockerfile)
        self.assertNotIn("antigravity.google", dockerfile)
        self.assertNotIn("bun.com/install", dockerfile)
        self.assertNotIn("fvm.app/install.sh", dockerfile)
        self.assertNotIn("nvm-sh/nvm", dockerfile)

    def test_derived_image_generates_allowlisted_dockerfile(self) -> None:
        launch = launch_config.LaunchConfig(
            version=1,
            tools={"opencode": False},
            apt=("ripgrep", "jq"),
            env={},
            ports=(),
            command=("sleep", "infinity"),
            path=None,
            source_hash="",
        )
        with (
            patch.object(docker, "run_state_dir", return_value=self.temp_dir),
            patch.object(docker, "image_exists", return_value=False),
            patch.object(docker, "image_id", return_value="derived-image-id"),
            patch.object(docker, "run") as run,
        ):
            image_name, image_id = docker.ensure_launch_image(
                "Some Owner",
                "Some.Repo",
                launch,
                "sha256:base",
            )

        self.assertRegex(
            image_name,
            r"^devbox-some-owner-some-repo-runtime-[a-f0-9]{16}$",
        )
        self.assertEqual(image_id, "derived-image-id")
        dockerfile = (self.temp_dir / "build" / "Dockerfile").read_text(encoding="utf-8")
        self.assertEqual(
            dockerfile,
            """\
FROM devbox-base

RUN apt-get update \\
    && apt-get install -y --no-install-recommends \\
        ripgrep \\
        jq \\
    && apt-get clean \\
    && rm -rf /var/lib/apt/lists/*
""",
        )
        run.assert_called_once_with(
            ["docker", "build", "-t", image_name, str(self.temp_dir / "build")],
            stream=True,
        )

    def test_empty_apt_uses_base_image(self) -> None:
        image_name, image_id = docker.ensure_launch_image(
            "owner",
            "repo",
            launch_config.default_config(),
            "sha256:base",
        )
        self.assertEqual((image_name, image_id), ("devbox-base", "sha256:base"))
        self.assertFalse((self.temp_dir / "build").exists())

    def test_node_tool_installs_nvm_and_resolves_configured_version(self) -> None:
        launch = launch_config.LaunchConfig(
            version=1,
            tools={"opencode": False, "node": "24"},
            apt=(),
            env={},
            ports=(),
            command=("sleep", "infinity"),
            path=None,
            source_hash="",
        )
        with (
            patch.object(docker, "run_state_dir", return_value=self.temp_dir),
            patch.object(docker, "image_exists", return_value=False),
            patch.object(docker, "image_id", return_value="node-image-id"),
            patch.object(docker, "run"),
        ):
            image_name, image_id = docker.ensure_launch_image(
                "owner",
                "repo",
                launch,
                "sha256:base",
            )

        self.assertNotEqual(image_name, "devbox-base")
        self.assertEqual(image_id, "node-image-id")
        dockerfile = (self.temp_dir / "build" / "Dockerfile").read_text(encoding="utf-8")
        self.assertEqual(
            dockerfile,
            """\
FROM devbox-base

ENV NVM_DIR=/opt/devbox/nvm
ENV BASH_ENV=/etc/devbox/bash-env
ENV PATH="/opt/devbox/nvm/current/bin:${PATH}"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN mkdir -p "$NVM_DIR" /etc/devbox \\
    && curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.6/install.sh \\
        | PROFILE=/dev/null NVM_DIR="$NVM_DIR" bash \\
    && . "$NVM_DIR/nvm.sh" \\
    && nvm install "24" \\
    && nvm alias default "24" \\
    && nvm use default \\
    && node_root="$(dirname "$(dirname "$(nvm which default)")")" \\
    && ln -sfn "$node_root" "$NVM_DIR/current" \\
    && printf '%s\\n' \\
        'export NVM_DIR=/opt/devbox/nvm' \\
        '[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"' \\
        > /etc/devbox/nvm-init.sh \\
    && ln -sf /etc/devbox/nvm-init.sh /etc/profile.d/devbox-nvm.sh \\
    && ln -sf /etc/devbox/nvm-init.sh /etc/devbox/bash-env \\
    && printf '%s\\n' '[ -r /etc/devbox/nvm-init.sh ] && . /etc/devbox/nvm-init.sh' \\
        >> /etc/bash.bashrc \\
    && chmod -R a+rwX "$NVM_DIR"
""",
        )
        self.assertNotIn("FROM node:", dockerfile)

    def test_node_tool_installs_multiple_versions_and_defaults_to_first(self) -> None:
        launch = launch_config.LaunchConfig(
            version=1,
            tools={"node": ("22", "16")},
            apt=(),
            env={},
            ports=(),
            command=("sleep", "infinity"),
            path=None,
            source_hash="",
        )
        with (
            patch.object(docker, "run_state_dir", return_value=self.temp_dir),
            patch.object(docker, "image_exists", return_value=False),
            patch.object(docker, "image_id", return_value="node-image-id"),
            patch.object(docker, "run"),
        ):
            docker.ensure_launch_image(
                "owner",
                "repo",
                launch,
                "sha256:base",
            )

        dockerfile = (self.temp_dir / "build" / "Dockerfile").read_text(encoding="utf-8")
        node_22 = dockerfile.index('nvm install "22"')
        node_16 = dockerfile.index('nvm install "16"')
        default = dockerfile.index('nvm alias default "22"')
        self.assertLess(node_22, node_16)
        self.assertLess(node_16, default)

    def test_bun_tool_installs_pinned_binary_on_path(self) -> None:
        launch = launch_config.LaunchConfig(
            version=1,
            tools={"bun": "1.3.9"},
            apt=(),
            env={},
            ports=(),
            command=("sleep", "infinity"),
            path=None,
            source_hash="",
        )
        with (
            patch.object(docker, "run_state_dir", return_value=self.temp_dir),
            patch.object(docker, "image_exists", return_value=False),
            patch.object(docker, "image_id", return_value="bun-image-id"),
            patch.object(docker, "run"),
        ):
            image_name, image_id = docker.ensure_launch_image(
                "owner",
                "repo",
                launch,
                "sha256:base",
            )

        self.assertNotEqual(image_name, "devbox-base")
        self.assertEqual(image_id, "bun-image-id")
        dockerfile = (self.temp_dir / "build" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("ENV BUN_INSTALL=/opt/devbox/bun", dockerfile)
        self.assertIn('ENV PATH="/opt/devbox/bun/bin:${PATH}"', dockerfile)
        self.assertIn("apt-get install -y --no-install-recommends unzip", dockerfile)
        self.assertIn("https://bun.com/install", dockerfile)
        self.assertIn('"bun-v1.3.9"', dockerfile)
        self.assertIn('test "$(bun --version)" = "1.3.9"', dockerfile)

    def test_opencode_tool_installs_binary_outside_build_user_home(self) -> None:
        launch = launch_config.LaunchConfig(
            version=1,
            tools={"opencode": True},
            apt=(),
            env={},
            ports=(),
            command=("sleep", "infinity"),
            path=None,
            source_hash="",
        )
        with (
            patch.object(docker, "run_state_dir", return_value=self.temp_dir),
            patch.object(docker, "image_exists", return_value=False),
            patch.object(docker, "image_id", return_value="opencode-image-id"),
            patch.object(docker, "run"),
        ):
            image_name, image_id = docker.ensure_launch_image(
                "owner",
                "repo",
                launch,
                "sha256:base",
            )

        self.assertNotEqual(image_name, "devbox-base")
        self.assertEqual(image_id, "opencode-image-id")
        dockerfile = (self.temp_dir / "build" / "Dockerfile").read_text(encoding="utf-8")
        self.assertEqual(
            dockerfile,
            """\
FROM devbox-base

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN curl -fsSL https://opencode.ai/install \\
        | HOME=/root SHELL=/bin/bash bash -s -- --no-modify-path \\
    && install -m 0755 /root/.opencode/bin/opencode /usr/local/bin/opencode \\
    && rm -rf /root/.opencode \\
    && opencode --version
""",
        )

    def test_codex_tool_installs_standalone_cli_without_credentials(self) -> None:
        launch = launch_config.LaunchConfig(
            version=1,
            tools={"codex": True, "opencode": False},
            apt=(),
            env={},
            ports=(),
            command=("sleep", "infinity"),
            path=None,
            source_hash="",
        )
        with (
            patch.object(docker, "run_state_dir", return_value=self.temp_dir),
            patch.object(docker, "image_exists", return_value=False),
            patch.object(docker, "image_id", return_value="codex-image-id"),
            patch.object(docker, "run"),
        ):
            image_name, image_id = docker.ensure_launch_image(
                "owner",
                "repo",
                launch,
                "sha256:base",
            )

        self.assertNotEqual(image_name, "devbox-base")
        self.assertEqual(image_id, "codex-image-id")
        dockerfile = (self.temp_dir / "build" / "Dockerfile").read_text(encoding="utf-8")
        self.assertEqual(
            dockerfile,
            """\
FROM devbox-base

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN mkdir -p /opt/devbox/codex-cli \\
    && curl -fsSL https://chatgpt.com/codex/install.sh \\
        | HOME=/root \\
          CODEX_HOME=/opt/devbox/codex-cli \\
          CODEX_INSTALL_DIR=/usr/local/bin \\
          CODEX_NON_INTERACTIVE=1 sh \\
    && chmod -R a+rX /opt/devbox/codex-cli \\
    && codex --version
""",
        )
        self.assertNotIn("OPENAI_API_KEY", dockerfile)
        self.assertNotIn("auth.json", dockerfile)

    def test_claude_tool_installs_signed_stable_package_without_credentials(self) -> None:
        launch = launch_config.LaunchConfig(
            version=1,
            tools={"claude": True},
            apt=(),
            env={},
            ports=(),
            command=("sleep", "infinity"),
            path=None,
            source_hash="",
        )
        with (
            patch.object(docker, "run_state_dir", return_value=self.temp_dir),
            patch.object(docker, "image_exists", return_value=False),
            patch.object(docker, "image_id", return_value="claude-image-id"),
            patch.object(docker, "run"),
        ):
            image_name, image_id = docker.ensure_launch_image(
                "owner",
                "repo",
                launch,
                "sha256:base",
            )

        self.assertNotEqual(image_name, "devbox-base")
        self.assertEqual(image_id, "claude-image-id")
        dockerfile = (self.temp_dir / "build" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("https://downloads.claude.ai/keys/claude-code.asc", dockerfile)
        self.assertIn("https://downloads.claude.ai/claude-code/apt/stable", dockerfile)
        self.assertIn("apt-get install -y --no-install-recommends claude-code", dockerfile)
        self.assertIn("claude --version", dockerfile)
        self.assertNotIn("ANTHROPIC_API_KEY", dockerfile)
        self.assertNotIn(".credentials.json", dockerfile)

    def test_agy_tool_installs_binary_outside_build_user_home(self) -> None:
        launch = launch_config.LaunchConfig(
            version=1,
            tools={"agy": True},
            apt=(),
            env={},
            ports=(),
            command=("sleep", "infinity"),
            path=None,
            source_hash="",
        )
        with (
            patch.object(docker, "run_state_dir", return_value=self.temp_dir),
            patch.object(docker, "image_exists", return_value=False),
            patch.object(docker, "image_id", return_value="agy-image-id"),
            patch.object(docker, "run"),
        ):
            image_name, image_id = docker.ensure_launch_image(
                "owner",
                "repo",
                launch,
                "sha256:base",
            )

        self.assertNotEqual(image_name, "devbox-base")
        self.assertEqual(image_id, "agy-image-id")
        dockerfile = (self.temp_dir / "build" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("https://antigravity.google/cli/install.sh", dockerfile)
        self.assertIn("--dir /opt/devbox/agy/bin", dockerfile)
        self.assertIn("ln -s /opt/devbox/agy/bin/agy /usr/local/bin/agy", dockerfile)
        self.assertIn("agy --version", dockerfile)
        self.assertNotIn("GOOGLE_API_KEY", dockerfile)

    def test_fvm_tool_installs_manager_with_persistent_sdk_cache(self) -> None:
        launch = launch_config.LaunchConfig(
            version=1,
            tools={"fvm": True},
            apt=(),
            env={},
            ports=(),
            command=("sleep", "infinity"),
            path=None,
            source_hash="",
        )
        with (
            patch.object(docker, "run_state_dir", return_value=self.temp_dir),
            patch.object(docker, "image_exists", return_value=False),
            patch.object(docker, "image_id", return_value="fvm-image-id"),
            patch.object(docker, "run"),
        ):
            image_name, image_id = docker.ensure_launch_image(
                "owner",
                "repo",
                launch,
                "sha256:base",
            )

        self.assertNotEqual(image_name, "devbox-base")
        self.assertEqual(image_id, "fvm-image-id")
        dockerfile = (self.temp_dir / "build" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("ENV FVM_INSTALL_DIR=/opt/devbox/fvm", dockerfile)
        self.assertIn("ENV FVM_CACHE_PATH=/devbox-home/.cache/fvm", dockerfile)
        self.assertIn('ENV PATH="/opt/devbox/fvm/bin:${PATH}"', dockerfile)
        self.assertIn("https://fvm.app/install.sh", dockerfile)
        self.assertIn("FVM_INSTALL_DIR=\"$FVM_INSTALL_DIR\" CI=1 bash", dockerfile)
        self.assertIn("fvm --version", dockerfile)
        self.assertNotIn("fvm install", dockerfile)
        self.assertNotIn("flutter doctor", dockerfile)

    def test_create_container_uses_ports_image_and_command_as_argv(self) -> None:
        with (
            patch.object(docker.platform, "system", return_value="Windows"),
            patch.object(
                docker,
                "run",
                return_value=CompletedProcess([], 0, "", ""),
            ) as run,
        ):
            docker.create_container(
                name="devbox-owner-repo",
                label_digest="digest",
                repo_root=self.temp_dir,
                home_path=self.temp_dir / "home",
                mode="NO-PR",
                env={"A": "one"},
                run_path=None,
                image_name="custom-image",
                ports=[3000, 5173],
                command=["python", "-m", "http.server"],
            )

        args = run.call_args.args[0]
        self.assertIn(["-p", "3000:3000"], [args[index : index + 2] for index in range(len(args) - 1)])
        self.assertIn(["-p", "5173:5173"], [args[index : index + 2] for index in range(len(args) - 1)])
        self.assertIn(
            ["-v", f"{self.temp_dir / 'home'}:/devbox-home"],
            [args[index : index + 2] for index in range(len(args) - 1)],
        )
        self.assertEqual(args[-4:], ["custom-image", "python", "-m", "http.server"])
        self.assertNotIn("/devbox-run", " ".join(args))

    def test_fingerprint_changes_with_each_launch_input(self) -> None:
        common = {
            "image": "sha256:image",
            "mode": "NO-PR",
            "repo_root": self.temp_dir,
            "default_branch": "main",
            "home_path": self.temp_dir / "home",
            "run_path": None,
            "ai_env_path": self.temp_dir / "ai.env",
            "launch_config_hash": "normalized",
            "ports": [3000],
            "command": ["sleep", "infinity"],
            "launch_source_hash": "source",
        }
        baseline = docker.fingerprint(**common)
        for key, value in {
            "image": "sha256:other",
            "home_path": self.temp_dir / "other-home",
            "launch_config_hash": "other-normalized",
            "ports": [5173],
            "command": ["python"],
            "launch_source_hash": "other-source",
        }.items():
            with self.subTest(key=key):
                changed = {**common, key: value}
                self.assertNotEqual(baseline, docker.fingerprint(**changed))


class StartLaunchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path.cwd() / ".test-tmp" / "start-launch" / self._testMethodName
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def test_launch_env_overrides_builtins_and_secrets_override_launch(self) -> None:
        secrets_dir = self.temp_dir / "secrets"
        secrets_dir.mkdir(exist_ok=True)
        (secrets_dir / "ai.env").write_text(
            "SHARED=secret\nSECRET_ONLY=yes\nHOME=/secret-home\n",
            encoding="utf-8",
        )
        with patch.object(start.paths, "secrets_dir", return_value=secrets_dir):
            env = start._container_env(
                None,
                "owner",
                "repo",
                "main",
                "NO-PR",
                {
                    "MODE": "from-launch",
                    "SHARED": "launch",
                    "LAUNCH_ONLY": "yes",
                    "HOME": "/launch-home",
                },
            )

        self.assertEqual(env["MODE"], "from-launch")
        self.assertEqual(env["SHARED"], "secret")
        self.assertEqual(env["LAUNCH_ONLY"], "yes")
        self.assertEqual(env["SECRET_ONLY"], "yes")
        self.assertEqual(env["HOME"], "/devbox-home")

    def test_invalid_launch_config_fails_before_any_docker_action(self) -> None:
        ctx = RepoContext(
            root=self.temp_dir,
            owner="owner",
            repo="repo",
            origin_url="https://github.com/owner/repo.git",
        )
        with (
            patch.object(start, "resolve_repo", return_value=ctx),
            patch.object(start, "_containment_guard"),
            patch.object(
                start.launch_config,
                "load",
                side_effect=DevboxError("invalid launch config"),
            ),
            patch.object(start.docker, "docker_info") as docker_info,
            patch.object(start.docker, "remove_container") as remove_container,
        ):
            with self.assertRaisesRegex(DevboxError, "invalid launch config"):
                start.run(str(self.temp_dir))

        docker_info.assert_not_called()
        remove_container.assert_not_called()


if __name__ == "__main__":
    unittest.main()
