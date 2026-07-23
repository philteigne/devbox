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
        self.assertIsNone(config.path)
        self.assertEqual(config.source_hash, "")

    def test_valid_config_is_normalized(self) -> None:
        body = """\
version: 1
tools:
  opencode: false
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

        self.assertEqual(config.tools, {"opencode": False})
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

    def test_derived_image_generates_allowlisted_dockerfile(self) -> None:
        launch = launch_config.LaunchConfig(
            version=1,
            tools={"opencode": True},
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
        self.assertEqual(args[-4:], ["custom-image", "python", "-m", "http.server"])
        self.assertNotIn("/devbox-run", " ".join(args))

    def test_fingerprint_changes_with_each_launch_input(self) -> None:
        common = {
            "image": "sha256:image",
            "mode": "NO-PR",
            "repo_root": self.temp_dir,
            "default_branch": "main",
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
            "SHARED=secret\nSECRET_ONLY=yes\n",
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
                },
            )

        self.assertEqual(env["MODE"], "from-launch")
        self.assertEqual(env["SHARED"], "secret")
        self.assertEqual(env["LAUNCH_ONLY"], "yes")
        self.assertEqual(env["SECRET_ONLY"], "yes")

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
