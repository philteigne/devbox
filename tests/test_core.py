from __future__ import annotations

import unittest
from contextlib import ExitStack, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from devbox.commands import init, start
from devbox.core import branch_protection, config, docker
from devbox.core.errors import DevboxError
from devbox.core.envfile import read_env, write_env
from devbox.core.gitctx import RepoContext, parse_github_origin
from devbox.core.token_file import write_atomic


class GitContextTests(unittest.TestCase):
    def test_parse_supported_github_origins(self) -> None:
        self.assertEqual(parse_github_origin("https://github.com/owner/repo.git"), ("owner", "repo"))
        self.assertEqual(parse_github_origin("git@github.com:owner/repo.git"), ("owner", "repo"))
        self.assertEqual(parse_github_origin("ssh://git@github.com/owner/repo.git"), ("owner", "repo"))

    def test_rejects_non_github_origin(self) -> None:
        self.assertIsNone(parse_github_origin("https://example.com/owner/repo.git"))


class BranchProtectionTests(unittest.TestCase):
    def test_required_review_detection(self) -> None:
        self.assertFalse(branch_protection.has_required_review(None))
        self.assertFalse(branch_protection.has_required_review({"required_pull_request_reviews": {"required_approving_review_count": 0}}))
        self.assertTrue(branch_protection.has_required_review({"required_pull_request_reviews": {"required_approving_review_count": 1}}))

    def test_payload_requires_at_least_one_review(self) -> None:
        payload = branch_protection.build_payload({"required_pull_request_reviews": {"required_approving_review_count": 0}})
        self.assertEqual(payload["required_pull_request_reviews"]["required_approving_review_count"], 1)

    def test_app_bypass_detection(self) -> None:
        protection = {
            "required_pull_request_reviews": {
                "bypass_pull_request_allowances": {
                    "apps": [{"slug": "devbox-app"}],
                }
            }
        }
        self.assertTrue(branch_protection.app_bypasses_reviews(protection, "devbox-app"))
        self.assertFalse(branch_protection.app_bypasses_reviews(protection, "other-app"))


class DockerNamingTests(unittest.TestCase):
    def test_container_name_uses_repo_id_disambiguator(self) -> None:
        first = docker.container_spec("Owner", "my.repo", "123456789")
        second = docker.container_spec("Owner", "my-repo", "987654321")
        self.assertNotEqual(first.name, second.name)


class EnvAndTokenTests(unittest.TestCase):
    def test_env_roundtrip_and_atomic_token_write(self) -> None:
        case_dir = Path.cwd() / ".test-tmp" / "env-token"
        case_dir.mkdir(parents=True, exist_ok=True)
        env_path = case_dir / "config.env"
        write_env(env_path, {"A": "1", "B": "two # literal"}, ["A", "B"])
        self.assertEqual(read_env(env_path), {"A": "1", "B": "two"})

        token_path = case_dir / "token"
        write_atomic(token_path, "secret")
        self.assertEqual(token_path.read_text(encoding="utf-8").strip(), "secret")


class ConfigTests(unittest.TestCase):
    def test_pr_eligibility_requires_both_safety_flags(self) -> None:
        project = config.ProjectConfig(
            owner="owner",
            repo="repo",
            repo_id="1",
            default_branch="main",
            installation_id="2",
            branch_protection="enforced",
            app_repo_access="granted",
        )
        self.assertTrue(project.pr_eligible)
        project.app_repo_access = "missing"
        self.assertFalse(project.pr_eligible)


class InitSafetyTests(unittest.TestCase):
    def test_non_personal_repo_stops_before_app_or_repo_access(self) -> None:
        context = RepoContext(
            root=Path("/tmp/butterfly"),
            owner="dropmobility",
            repo="butterfly",
            origin_url="git@github.com:dropmobility/butterfly.git",
        )
        with ExitStack() as stack:
            stack.enter_context(patch.object(init, "resolve_repo", return_value=context))
            stack.enter_context(patch.object(init.gh, "require_gh_auth", return_value="token"))
            stack.enter_context(patch.object(init.gh, "user", return_value={"login": "philteigne"}))
            repo = stack.enter_context(patch.object(init.gh, "repo"))
            load_identity = stack.enter_context(patch.object(init.github_app, "load_identity"))
            with self.assertRaisesRegex(DevboxError, "NO-PR mode"):
                init.run()

        repo.assert_not_called()
        load_identity.assert_not_called()


class StartSafetyTests(unittest.TestCase):
    def test_uninitialized_repo_starts_without_github_credentials(self) -> None:
        context = RepoContext(
            root=Path("/tmp/butterfly"),
            owner="dropmobility",
            repo="butterfly",
            origin_url="git@github.com:dropmobility/butterfly.git",
        )
        with ExitStack() as stack:
            stack.enter_context(patch.object(start, "resolve_repo", return_value=context))
            stack.enter_context(
                patch.object(
                    start.launch_config,
                    "load",
                    return_value=start.launch_config.default_config(),
                )
            )
            stack.enter_context(patch.object(start.docker, "docker_info"))
            stack.enter_context(patch.object(start.config, "read_project_config", return_value=None))
            stack.enter_context(patch.object(start.docker, "ensure_base_image", return_value="image"))
            stack.enter_context(
                patch.object(
                    start.docker,
                    "run_state_dir",
                    return_value=Path.cwd() / ".test-tmp" / "devbox-run-test",
                )
            )
            stack.enter_context(patch.object(start, "read_env", return_value={}))
            stack.enter_context(patch.object(start.docker, "fingerprint", return_value="fingerprint"))
            stack.enter_context(patch.object(start.docker, "inspect_container", return_value=None))
            create_container = stack.enter_context(patch.object(start.docker, "create_container"))
            stack.enter_context(patch.object(start.docker, "write_fingerprint"))
            require_auth = stack.enter_context(patch.object(start.gh, "require_gh_auth"))
            load_identity = stack.enter_context(patch.object(start.github_app, "load_identity"))
            output = StringIO()
            with redirect_stdout(output):
                start.run()

        require_auth.assert_not_called()
        load_identity.assert_not_called()
        self.assertEqual(create_container.call_args.kwargs["mode"], "NO-PR")
        self.assertIsNone(create_container.call_args.kwargs["run_path"])
        self.assertEqual(
            create_container.call_args.kwargs["home_path"],
            Path.cwd() / ".test-tmp" / "devbox-run-test" / "home",
        )
        self.assertEqual(create_container.call_args.kwargs["env"]["HOME"], "/devbox-home")
        self.assertNotIn("GH_TOKEN", create_container.call_args.kwargs["env"])
        self.assertIn("Mode: NO-PR", output.getvalue())


if __name__ == "__main__":
    unittest.main()
