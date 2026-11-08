import json
import re
import tempfile
import unittest
from pathlib import Path

from factory_ci import appversion, description, images
from tests.helpers import GitRepo

REPO_ROOT = Path(__file__).resolve().parents[3]
SHA = "f4c0eff3a1d8d5f3ead470d0c7405c3ee37da24e"
KEY = "org.opencontainers.image.description"

VERSION_PIN = (
    "# renovate: datasource=repology depName=alpine_edge/snapcast\n"
    "ARG SNAPCAST_VERSION=0.35.0-r1\n"
)
REV_PIN = (
    "# renovate: datasource=git-refs depName=https://github.com/justinfrankel/ninjam branch=main\n"
    f"ARG NINJAM_REV={SHA}\n"
)


def renovate_patterns(file_pattern_fragment: str) -> list[re.Pattern]:
    """The renovate.json regex manager patterns for files matching the fragment, in Python syntax."""
    config = json.loads((REPO_ROOT / "renovate.json").read_text())
    found = []
    for manager in config["customManagers"]:
        if any(file_pattern_fragment in p for p in manager["managerFilePatterns"]):
            found += [re.compile(s.replace("(?<", "(?P<")) for s in manager["matchStrings"]]
    return found


class ParseTest(unittest.TestCase):
    def test_version_pin(self):
        [v] = appversion.app_versions("snapcast", "FROM x\n" + VERSION_PIN)
        self.assertEqual(v, appversion.AppVersion(
            "snapcast", "0.35.0-r1", False, "repology", "alpine_edge/snapcast"))
        self.assertEqual(str(v), "snapcast 0.35.0-r1")

    def test_revision_pin_shows_short_sha(self):
        [v] = appversion.app_versions("ninjam", REV_PIN)
        self.assertTrue(v.is_revision)
        self.assertEqual(v.value, SHA)
        self.assertEqual(v.branch, "main")
        self.assertEqual(str(v), "ninjam f4c0eff")

    def test_unannotated_args_are_ignored(self):
        text = 'ARG IMAGE_SOURCE\nARG FOO_VERSION=1.0\n# a comment\nARG BAR_REV=abc\n'
        self.assertEqual(appversion.app_versions("demo", text), [])

    def test_multiple_pins_in_file_order_and_underscores(self):
        text = REV_PIN + "# renovate: datasource=pypi depName=foo-bar\nARG FOO_BAR_VERSION=2.0\n"
        versions = appversion.app_versions("demo", text)
        self.assertEqual([v.name for v in versions], ["ninjam", "foo-bar"])
        self.assertEqual(appversion.summary(versions), "ninjam f4c0eff, foo-bar 2.0")

    def test_short_revision_is_rejected(self):
        text = REV_PIN.replace(SHA, "f4c0eff")
        with self.assertRaisesRegex(ValueError, "ninjam: NINJAM_REV must be a full 40-character commit sha"):
            appversion.app_versions("ninjam", text)

    def test_revision_needs_branch(self):
        text = REV_PIN.replace(" branch=main", "")
        with self.assertRaisesRegex(ValueError, "needs branch="):
            appversion.app_versions("ninjam", text)

    def test_annotation_needs_datasource_and_dep_name(self):
        text = "# renovate: datasource=repology\nARG SNAPCAST_VERSION=1\n"
        with self.assertRaisesRegex(ValueError, "needs datasource= and depName="):
            appversion.app_versions("snapcast", text)

    def test_source_link_only_for_github_revisions(self):
        [rev] = appversion.app_versions("ninjam", REV_PIN)
        [ver] = appversion.app_versions("snapcast", VERSION_PIN)
        self.assertEqual(appversion.source_link(rev),
                         f"https://github.com/justinfrankel/ninjam/commit/{SHA}")
        self.assertIsNone(appversion.source_link(ver))


class ReadmeCellTest(unittest.TestCase):
    def test_cells(self):
        self.assertEqual(appversion.readme_cell([]), "—")
        self.assertEqual(
            appversion.readme_cell(appversion.app_versions("snapcast", VERSION_PIN)),
            "snapcast <!-- renovate: datasource=repology depName=alpine_edge/snapcast -->`0.35.0-r1`")
        self.assertEqual(
            appversion.readme_cell(appversion.app_versions("ninjam", REV_PIN)),
            "ninjam <!-- renovate: datasource=git-refs depName=https://github.com/justinfrankel/ninjam "
            f"branch=main digest={SHA} -->`f4c0eff`")

    def test_renovate_reads_the_same_pin_from_readme_and_dockerfile(self):
        readme_patterns = renovate_patterns("README")
        dockerfile_patterns = renovate_patterns("Dockerfile")
        for pin in (VERSION_PIN, REV_PIN):
            with self.subTest(pin=pin.splitlines()[1]):
                [v] = appversion.app_versions("demo", pin)
                cell = appversion.readme_cell([v])
                from_readme = [m.groupdict() for p in readme_patterns for m in p.finditer(cell)]
                from_dockerfile = [m.groupdict() for p in dockerfile_patterns for m in p.finditer(pin)]
                self.assertEqual(len(from_readme), 1)
                self.assertEqual(len(from_dockerfile), 1)
                for key in ("datasource", "depName", "currentValue", "currentDigest"):
                    self.assertEqual(from_readme[0].get(key), from_dockerfile[0].get(key), key)


class RepoConsistencyTest(unittest.TestCase):
    def test_readme_application_cells_match_dockerfiles(self):
        readme = (REPO_ROOT / "README.md").read_text().splitlines()
        for image in images.list_images(REPO_ROOT):
            with self.subTest(image=image):
                rows = [line for line in readme if line.startswith(f"| `{image}` | ")]
                self.assertEqual(len(rows), 1, "one README table row per image")
                versions = appversion.app_versions(image, images.read_dockerfile(REPO_ROOT, image))
                cell = appversion.readme_cell(versions)
                self.assertIn(f" | {cell} | ", rows[0],
                              f"run: python3 .github/scripts/ci.py readme-cell --image {image}")
                self.assertEqual(rows[0].count("<!-- renovate:"), len(versions))


class DescriptionTest(unittest.TestCase):
    def test_description_includes_application_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "snapcast").mkdir()
            (root / "snapcast" / "Dockerfile").write_text(
                f'FROM x\nLABEL {KEY}="Snapcast server."\n' + VERSION_PIN)
            self.assertTrue(description.package_description(root, "snapcast").startswith(
                "Snapcast server. Application: snapcast 0.35.0-r1. Signed multi-arch image."))

    def test_description_reads_dockerfile_at_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = GitRepo(tmp)
            repo.write("ninjam/Dockerfile", f'FROM x\nLABEL {KEY}="Old."\n' + REV_PIN)
            sha = repo.commit("first")
            repo.write("ninjam/Dockerfile", f'FROM x\nLABEL {KEY}="New."\n')
            repo.commit("second")
            self.assertTrue(description.package_description(repo.root, "ninjam", sha)
                            .startswith("Old. Application: ninjam f4c0eff. Signed"))
            self.assertTrue(description.package_description(repo.root, "ninjam")
                            .startswith("New. Signed"))


if __name__ == "__main__":
    unittest.main()
