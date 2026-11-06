import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

import ci
from factory_ci import registry
from tests.helpers import GitRepo


class ReleasePlanTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = GitRepo(tmp.name)
        self.root = self.repo.root
        for image in ("alpine3", "ninjam"):
            self.repo.write(f"{image}/Dockerfile", "FROM scratch\n")
        self.repo.commit("chore: initial")
        self.repo.tag("ninjam/v1.0.0")  # released, no changes since
        self.repo.write("alpine3/Dockerfile", "FROM scratch\n# change\n")
        self.head = self.repo.commit("feat(alpine3): change", "alpine3/Dockerfile")

    def test_build_when_version_absent(self):
        plans = ci.release_plan(self.root, self.head, lambda ref: None)
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["image"], "alpine3")
        self.assertEqual(plans[0]["version"], "1.0.0")
        self.assertEqual(plans[0]["previous_version"], None)
        self.assertEqual(plans[0]["bump"], "initial")
        self.assertEqual(plans[0]["mode"], "build")

    def test_lookup_uses_registry_ref(self):
        refs = []
        ci.release_plan(self.root, self.head, lambda ref: refs.append(ref))
        self.assertEqual(refs, ["ghcr.io/bill-of-materials/alpine3:1.0.0"])

    def test_resume_when_same_revision(self):
        plans = ci.release_plan(self.root, self.head, lambda ref: self.head)
        self.assertEqual(plans[0]["mode"], "resume")
        out = self._cli(lambda ref: self.head)
        self.assertIn("matrix=" + ci.json.dumps([ci.RELEASE_NONE_ENTRY], separators=(",", ":")), out)
        self.assertIn("has_builds=false", out)
        self.assertIn("has_releases=true", out)

    def test_fails_when_other_revision(self):
        with self.assertRaises(RuntimeError) as cm:
            ci.release_plan(self.root, self.head, lambda ref: "deadbeef")
        self.assertIn("already published", str(cm.exception))
        self.assertIn("alpine3:1.0.0", str(cm.exception))

    def test_images_without_changes_excluded(self):
        plans = ci.release_plan(self.root, self.head, lambda ref: None)
        self.assertNotIn("ninjam", [p["image"] for p in plans])

    def test_cli_build_matrix_has_version(self):
        out = self._cli(lambda ref: None)
        self.assertIn("has_builds=true", out)
        self.assertIn("has_releases=true", out)
        self.assertIn('"version":"1.0.0"', out)
        self.assertIn('"platform":"linux/arm64"', out)

    def _cli(self, lookup):
        args = ci.build_parser().parse_args(
            ["--root", str(self.root), "release-plan", "--head-sha", self.head])
        buf = io.StringIO()
        with mock.patch.object(ci.registry, "index_revision", lookup), redirect_stdout(buf):
            self.assertEqual(args.func(args), 0)
        return buf.getvalue()


class IndexRevisionTest(unittest.TestCase):
    def _run(self, returncode, stdout="", stderr=""):
        return mock.patch.object(
            registry.subprocess, "run",
            return_value=subprocess.CompletedProcess([], returncode, stdout, stderr))

    def test_reads_revision_annotation(self):
        out = '{"annotations": {"org.opencontainers.image.revision": "abc"}}'
        with self._run(0, out):
            self.assertEqual(registry.index_revision("r/x:1.0.0"), "abc")

    def test_missing_annotation_is_none(self):
        with self._run(0, "{}"):
            self.assertIsNone(registry.index_revision("r/x:1.0.0"))

    def test_absent_classification(self):
        for err in ("Error: MANIFEST_UNKNOWN: manifest unknown", "NAME_UNKNOWN: repo",
                    "Error: Not Found"):
            with self._run(1, "", err):
                self.assertIsNone(registry.index_revision("r/x:1.0.0"))

    def test_other_errors_raise(self):
        with self._run(1, "", "UNAUTHORIZED: denied"):
            with self.assertRaises(RuntimeError):
                registry.index_revision("r/x:1.0.0")

    def test_digest_of(self):
        with self._run(0, "sha256:abc\n"):
            self.assertEqual(registry.digest_of("r/x:1"), "sha256:abc")
