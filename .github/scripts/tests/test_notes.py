import base64
import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

import ci
from factory_ci import notes, registry
from factory_ci.versioning import Commit

INDEX = "sha256:" + "a" * 64
AMD = "sha256:" + "b" * 64
ARM = "sha256:" + "c" * 64
SHA = "1234567890abcdef1234567890abcdef12345678"


def make_ctx(**overrides):
    values = dict(
        image="ninjam", version="1.4.2", previous_version="1.4.1", index_digest=INDEX,
        platforms=[("linux/amd64", AMD), ("linux/arm64", ARM)],
        commits=[Commit(SHA, "feat: add thing (#12)", ""), Commit("f" * 40, "chore: tidy", "")],
        repo="bill-of-materials/factory", server_url="https://github.com", sha=SHA, run_id="123",
        provenance_url="https://github.com/bill-of-materials/factory/attestations/1",
        sbom_urls={"linux/amd64": "https://github.com/bill-of-materials/factory/attestations/2"},
    )
    values.update(overrides)
    return notes.ReleaseContext(**values)


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.text = notes.render_release_notes(make_ctx())

    def test_pull_commands(self):
        self.assertIn("docker pull ghcr.io/bill-of-materials/ninjam:1.4.2", self.text)
        self.assertIn(f"docker pull ghcr.io/bill-of-materials/ninjam@{INDEX}", self.text)

    def test_platforms_with_digests(self):
        self.assertIn("linux/amd64", self.text)
        self.assertIn(AMD, self.text)
        self.assertIn(ARM, self.text)

    def test_run_link(self):
        self.assertIn("https://github.com/bill-of-materials/factory/actions/runs/123", self.text)

    def test_commit_and_pr_links(self):
        self.assertIn(f"https://github.com/bill-of-materials/factory/commit/{SHA}", self.text)
        self.assertIn("[#12](https://github.com/bill-of-materials/factory/pull/12)", self.text)

    def test_verify_commands(self):
        self.assertIn(
            "--certificate-identity-regexp "
            "'^https://github.com/bill-of-materials/factory/\\.github/workflows/release\\.yml@refs/heads/main$'",
            self.text)
        self.assertIn("--certificate-oidc-issuer https://token.actions.githubusercontent.com", self.text)
        self.assertIn(
            f"gh attestation verify oci://ghcr.io/bill-of-materials/ninjam@{INDEX} -R bill-of-materials/factory",
            self.text)
        self.assertIn("```bash", self.text)

    def test_attestation_links(self):
        self.assertIn("attestations/1", self.text)
        self.assertIn("attestations/2", self.text)

    def test_changes_heading(self):
        self.assertIn("Changes since 1.4.1", self.text)

    def test_first_release_heading(self):
        text = notes.render_release_notes(make_ctx(previous_version=None))
        self.assertIn("Changes (first release)", text)

    def test_missing_attestation_url_omits_line(self):
        text = notes.render_release_notes(make_ctx(provenance_url=None, sbom_urls={}))
        self.assertNotIn("None", text)
        self.assertNotIn("Provenance attestation", text)
        self.assertNotIn("SBOM attestation", text)
        self.assertIn("Attestations: https://github.com/bill-of-materials/factory/attestations", text)


def statement(predicate_type, predicate):
    payload = json.dumps({"predicateType": predicate_type, "predicate": predicate}).encode()
    return {"dsseEnvelope": {"payload": base64.b64encode(payload).decode()}}


class FetchAttestationsTest(unittest.TestCase):
    def fetch(self, bundles):
        def run(cmd, **kwargs):
            self.assertEqual(cmd[:3], ["gh", "attestation", "download"])
            (Path(kwargs["cwd"]) / "sha256-x.jsonl").write_text(
                "\n".join(json.dumps(b) for b in bundles) + "\n")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        with mock.patch.object(registry.subprocess, "run", run):
            return registry.fetch_attestations("bill-of-materials/factory", "r/x", AMD)

    def test_decode_attestation_payload(self):
        result = self.fetch([statement(notes.PROVENANCE_TYPE, {"buildType": "x"}),
                             statement("https://spdx.dev/Document/v2.3", {"spdxVersion": "SPDX-2.3"})])
        self.assertEqual(result[0].predicate_type, notes.PROVENANCE_TYPE)
        self.assertEqual(result[1].predicate, {"spdxVersion": "SPDX-2.3"})

    def test_decode_wrapped_bundle(self):
        result = self.fetch([{"bundle": statement(notes.PROVENANCE_TYPE, {"a": 1})}])
        self.assertEqual(result[0].predicate, {"a": 1})


class PlatformDigestsTest(unittest.TestCase):
    def test_skips_unknown_and_missing_platform(self):
        manifest = {"manifests": [
            {"digest": AMD, "platform": {"os": "linux", "architecture": "amd64"}},
            {"digest": ARM, "platform": {"os": "linux", "architecture": "arm", "variant": "v7"}},
            {"digest": "sha256:" + "d" * 64, "platform": {"os": "unknown", "architecture": "unknown"}},
            {"digest": "sha256:" + "e" * 64},
        ]}
        result = subprocess.CompletedProcess([], 0, json.dumps(manifest), "")
        with mock.patch.object(registry, "_crane", return_value=result):
            self.assertEqual(registry.platform_digests("r/x:1"),
                             [("linux/amd64", AMD), ("linux/arm/v7", ARM)])


class CliTest(unittest.TestCase):
    def run_cli(self, *extra, attestations=None):
        sbom = registry.Attestation("https://spdx.dev/Document/v2.3", {"spdxVersion": "SPDX-2.3"})
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "notes.md"
        self.sboms = Path(self.tmp.name) / "sbom"
        with mock.patch.object(registry, "digest_of", return_value=INDEX) as self.digest_of, \
                mock.patch.object(registry, "platform_digests", return_value=[("linux/arm/v7", ARM)]) as self.platform_digests, \
                mock.patch.object(registry, "fetch_attestations",
                                  return_value=[sbom] if attestations is None else attestations), \
                mock.patch.object(ci.versioning, "latest_tag", return_value="ninjam/v1.4.1"), \
                mock.patch.object(ci.versioning, "commits_since", return_value=[Commit(SHA, "fix: x", "")]):
            self.stderr = io.StringIO()
            with redirect_stderr(self.stderr):
                rc = ci.main(["release-notes", "--image", "ninjam", "--version", "1.4.2", "--sha", SHA,
                              "--run-id", "9", "--out", str(self.out), "--sbom-dir", str(self.sboms),
                              *extra])
        self.assertEqual(rc, 0)
        return self.out.read_text()

    def test_writes_notes_and_sboms(self):
        text = self.run_cli()
        self.assertIn("Changes since 1.4.1", text)
        self.assertEqual(
            json.loads((self.sboms / "ninjam-1.4.2-linux-arm-v7.spdx.json").read_text()),
            {"spdxVersion": "SPDX-2.3"})

    def test_attestation_url_flags_appear_in_notes(self):
        text = self.run_cli("--provenance-url", "https://github.com/o/r/attestations/1",
                            "--sbom-url", "linux/arm/v7=https://github.com/o/r/attestations/2")
        self.assertIn("https://github.com/o/r/attestations/1", text)
        self.assertIn("https://github.com/o/r/attestations/2", text)
        self.assertNotIn("None", text)

    def test_no_flags_links_generic_attestations_page(self):
        text = self.run_cli()
        self.assertIn("https://github.com/bill-of-materials/factory/attestations", text)
        self.assertNotIn("None", text)

    def test_missing_sbom_attestation_warns(self):
        self.run_cli(attestations=[])
        self.assertIn("::warning::", self.stderr.getvalue())
        self.assertIn("linux/arm/v7", self.stderr.getvalue())
        self.assertFalse(self.sboms.exists() and any(self.sboms.iterdir()))

    def test_sbom_present_no_warning(self):
        self.run_cli()
        self.assertEqual(self.stderr.getvalue(), "")

    def test_explicit_digest_skips_tag_lookup(self):
        other = "sha256:" + "9" * 64
        text = self.run_cli("--digest", other)
        self.digest_of.assert_not_called()
        self.platform_digests.assert_called_once_with(f"ghcr.io/bill-of-materials/ninjam@{other}")
        self.assertIn(other, text)

    def test_tag_lookup_without_explicit_digest(self):
        self.run_cli()
        self.digest_of.assert_called_once_with("ghcr.io/bill-of-materials/ninjam:1.4.2")


if __name__ == "__main__":
    unittest.main()
