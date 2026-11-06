import tempfile
import unittest

from factory_ci.versioning import (
    Commit, bump, bump_level, commits_since, latest_tag, next_version,
)
from tests.helpers import GitRepo


class BumpTest(unittest.TestCase):
    def test_bump_levels(self):
        self.assertEqual(bump_level(Commit("a", "feat(ninjam): add x", "")), "minor")
        self.assertEqual(bump_level(Commit("a", "chore(deps): update alpine digest", "")), "patch")
        self.assertEqual(bump_level(Commit("a", "fix!: drop root user", "")), "major")
        self.assertEqual(bump_level(Commit("a", "feat(base)!: rename user", "")), "major")
        self.assertEqual(bump_level(Commit("a", "fix: x", "body\n\nBREAKING CHANGE: y")), "major")
        self.assertEqual(bump_level(Commit("a", "Update stuff", "")), "patch")

    def test_bump(self):
        self.assertEqual(bump("1.2.3", "patch"), "1.2.4")
        self.assertEqual(bump("1.2.3", "minor"), "1.3.0")
        self.assertEqual(bump("1.2.3", "major"), "2.0.0")


class VersionPlanTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = GitRepo(tmp.name)
        self.root = self.repo.root
        for image in ("alpine3", "ninjam", "snapcast"):
            self.repo.write(f"{image}/Dockerfile", "FROM scratch\n")
        self.repo.commit("chore: initial")

    def test_first_release_is_1_0_0(self):
        self.repo.write("alpine3/Dockerfile", "FROM scratch\n# change\n")
        sha = self.repo.commit("feat(alpine3): change", "alpine3/Dockerfile")
        plan = next_version(self.root, "alpine3")
        self.assertEqual(plan.version, "1.0.0")
        self.assertIsNone(plan.previous_version)
        self.assertEqual(plan.bump, "initial")
        self.assertIn(sha, [c.sha for c in plan.commits])

    def test_no_release_without_changes(self):
        self.repo.tag("alpine3/v1.0.0")
        self.assertIsNone(next_version(self.root, "alpine3"))

    def test_markdown_only_change_no_release(self):
        self.repo.tag("ninjam/v1.0.0")
        self.repo.write("ninjam/README.md", "hi\n")
        self.repo.write("ninjam/docs/deep/notes.md", "hi\n")
        self.repo.commit("docs: readme")
        self.assertIsNone(next_version(self.root, "ninjam"))

    def test_highest_bump_wins(self):
        self.repo.tag("alpine3/v1.2.3")
        self.repo.write("alpine3/a", "1")
        self.repo.commit("chore(deps): bump")
        self.repo.write("alpine3/b", "2")
        self.repo.commit("feat: new thing")
        plan = next_version(self.root, "alpine3")
        self.assertEqual(plan.version, "1.3.0")
        self.assertEqual(plan.previous_version, "1.2.3")
        self.assertEqual(plan.bump, "minor")
        self.assertEqual(len(plan.commits), 2)
        self.assertEqual(plan.commits[0].subject, "feat: new thing")

    def test_semver_sort_not_lexical(self):
        self.repo.tag("alpine3/v1.9.0")
        self.repo.tag("alpine3/v1.10.0")
        self.repo.tag("alpine3/vfoo")
        self.assertEqual(latest_tag(self.root, "alpine3"), "alpine3/v1.10.0")

    def test_latest_tag_none(self):
        self.assertIsNone(latest_tag(self.root, "alpine3"))

    def test_commit_touching_two_images_bumps_both(self):
        self.repo.write("ninjam/Dockerfile", "FROM scratch\n# x\n")
        self.repo.write("snapcast/Dockerfile", "FROM scratch\n# x\n")
        sha = self.repo.commit("chore(deps): update base")
        for image in ("ninjam", "snapcast"):
            plan = next_version(self.root, image)
            self.assertEqual(plan.version, "1.0.0")
            self.assertIn(sha, [c.sha for c in plan.commits])

    def test_two_images_patch_after_tags(self):
        self.repo.tag("ninjam/v1.0.0")
        self.repo.tag("snapcast/v1.0.0")
        self.repo.write("ninjam/Dockerfile", "FROM scratch\n# x\n")
        self.repo.write("snapcast/Dockerfile", "FROM scratch\n# x\n")
        sha = self.repo.commit("chore(deps): update base")
        for image in ("ninjam", "snapcast"):
            plan = next_version(self.root, image)
            self.assertEqual(plan.version, "1.0.1")
            self.assertEqual(plan.bump, "patch")
            self.assertEqual([c.sha for c in plan.commits], [sha])

    def test_merge_commits_ignored(self):
        self.repo.tag("alpine3/v1.0.0")
        self.repo.checkout("feature", create=True)
        self.repo.write("alpine3/a", "1")
        self.repo.commit("fix: one")
        self.repo.write("alpine3/b", "2")
        self.repo.commit("fix: two")
        self.repo.checkout("main")
        self.repo.write("ninjam/z", "z")
        self.repo.commit("chore: other")
        merge = self.repo.merge("feature", "Merge feature")
        commits = commits_since(self.root, "alpine3", "alpine3/v1.0.0")
        self.assertEqual(len(commits), 2)
        self.assertNotIn(merge, [c.sha for c in commits])

    def test_other_image_tags_ignored(self):
        self.repo.tag("ninjam/v5.0.0")
        self.assertIsNone(latest_tag(self.root, "alpine3"))
        plan = next_version(self.root, "alpine3")
        self.assertEqual(plan.version, "1.0.0")

    def test_commit_body_parsed(self):
        self.repo.tag("alpine3/v1.0.0")
        self.repo.write("alpine3/a", "1")
        self.repo.commit("fix: x\n\nsome body\n\nBREAKING CHANGE: y")
        plan = next_version(self.root, "alpine3")
        self.assertEqual(plan.version, "2.0.0")
        self.assertIn("BREAKING CHANGE: y", plan.commits[0].body)


if __name__ == "__main__":
    unittest.main()
