from __future__ import annotations

import unittest
from pathlib import Path

from devbox.core import branch_protection, config, docker
from devbox.core.envfile import read_env, write_env
from devbox.core.gitctx import parse_github_origin
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


if __name__ == "__main__":
    unittest.main()
