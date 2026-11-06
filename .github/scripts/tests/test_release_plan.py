import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
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
        self.base = self.repo.commit("chore: initial")
        self.repo.tag("ninjam/v1.0.0")  # released, no changes since
        self.repo.write("alpine3/Dockerfile", "FROM scratch\n# change\n")
        self.head = self.repo.commit("feat(alpine3): change", "alpine3/Dockerfile")

    def plan(self, lookup):
        plans, errors = ci.release_plan(self.root, self.head, lookup)
        return plans, errors

    def test_build_when_version_absent(self):
        plans, errors = self.plan(lambda ref, **_: None)
        self.assertEqual(errors, [])
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["image"], "alpine3")
        self.assertEqual(plans[0]["version"], "1.0.0")
        self.assertEqual(plans[0]["previous_version"], None)
        self.assertEqual(plans[0]["bump"], "initial")
        self.assertEqual(plans[0]["mode"], "build")
        self.assertEqual(plans[0]["sha"], self.head)

    def test_lookup_uses_registry_ref(self):
        refs = []
        self.plan(lambda ref, **_: refs.append(ref))
        self.assertEqual(refs, ["ghcr.io/bill-of-materials/alpine3:1.0.0"])

    def test_resume_when_same_revision(self):
        plans, errors = self.plan(lambda ref, **_: self.head)
        self.assertEqual(errors, [])
        self.assertEqual(plans[0]["mode"], "resume")
        self.assertEqual(plans[0]["sha"], self.head)
        out = self._cli(lambda ref, **_: self.head)
        self.assertIn("matrix=" + ci.json.dumps([ci.RELEASE_NONE_ENTRY], separators=(",", ":")), out)
        self.assertIn("has_builds=false", out)
        self.assertIn("has_releases=true", out)
        self.assertIn("errors=[]", out)

    def test_resume_from_ancestor_revision(self):
        # partial publish from an earlier commit, then a new commit landed
        plans, errors = self.plan(lambda ref, **_: self.base)
        self.assertEqual(errors, [])
        self.assertEqual(plans[0]["mode"], "resume")
        self.assertEqual(plans[0]["sha"], self.base)

    def _change_ninjam(self):
        self.repo.write("ninjam/Dockerfile", "FROM scratch\n# fix\n")
        self.head = self.repo.commit("fix(ninjam): change", "ninjam/Dockerfile")

    def test_non_ancestor_revision_fails_only_that_image(self):
        self.repo.checkout("side", create=True)
        side = self.repo.commit("chore: elsewhere")
        self.repo.checkout("main")
        self._change_ninjam()

        def lookup(ref, **_):
            return side if "/alpine3:" in ref else None

        plans, errors = self.plan(lookup)
        self.assertEqual([(p["image"], p["mode"]) for p in plans], [("ninjam", "build")])
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["image"], "alpine3")
        self.assertEqual(errors[0]["version"], "1.0.0")
        self.assertIn(side, errors[0]["error"])

    def test_unknown_or_missing_commit_revision_is_error(self):
        for published in ("<unknown>", "deadbeef" * 5):
            plans, errors = self.plan(lambda ref, **_: published)
            self.assertEqual(plans, [])
            self.assertEqual([e["image"] for e in errors], ["alpine3"])
            self.assertIn("already published", errors[0]["error"])

    def test_cli_reports_errors_and_still_plans_healthy_images(self):
        self._change_ninjam()
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            out = self._cli(lambda ref, **_: "<unknown>" if "/alpine3:" in ref else None)
        self.assertIn("has_releases=true", out)
        self.assertIn('"image":"ninjam"', out)
        errors_line = next(line for line in out.splitlines() if line.startswith("errors="))
        self.assertEqual([e["image"] for e in json.loads(errors_line[len("errors="):])], ["alpine3"])
        self.assertIn("::error::alpine3", stderr.getvalue())

    def test_lookup_told_whether_image_was_ever_released(self):
        self._change_ninjam()
        calls = {}

        def lookup(ref, never_released):
            calls[ref.split("/")[-1]] = never_released

        self.plan(lookup)
        self.assertEqual(calls, {"alpine3:1.0.0": True, "ninjam:1.0.1": False})

    def test_images_without_changes_excluded(self):
        plans, _ = self.plan(lambda ref, **_: None)
        self.assertNotIn("ninjam", [p["image"] for p in plans])

    def test_cli_build_matrix_has_version(self):
        out = self._cli(lambda ref, **_: None)
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

    def test_missing_annotation_is_unknown(self):
        with self._run(0, "{}"):
            self.assertEqual(registry.index_revision("r/x:1.0.0"), "<unknown>")

    def test_absent_classification(self):
        for err in ("Error: MANIFEST_UNKNOWN: manifest unknown", "NAME_UNKNOWN: repo",
                    "Error: Not Found"):
            with self._run(1, "", err):
                self.assertIsNone(registry.index_revision("r/x:1.0.0"))

    def test_other_errors_raise(self):
        with self._run(1, "", "UNAUTHORIZED: denied"):
            with self.assertRaises(RuntimeError):
                registry.index_revision("r/x:1.0.0")

    def test_denied_is_absent_only_for_never_released_image(self):
        for err in ("DENIED: requested access to the resource is denied",
                    "UNAUTHORIZED: authentication required",
                    "GET https://ghcr.io/v2/x/manifests/1.0.0: unexpected status code 403 Forbidden"):
            with self._run(1, "", err):
                self.assertIsNone(registry.index_revision("r/x:1.0.0", never_released=True))
            with self._run(1, "", err):
                with self.assertRaises(RuntimeError):
                    registry.index_revision("r/x:1.0.0", never_released=False)

    def test_digest_of(self):
        with self._run(0, "sha256:abc\n"):
            self.assertEqual(registry.digest_of("r/x:1"), "sha256:abc")
