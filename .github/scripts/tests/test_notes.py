import base64
import json
import subprocess
import tempfile
import unittest
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


def statement(predicate_type, predicate):
    payload = json.dumps({"predicateType": predicate_type, "predicate": predicate}).encode()
    return {"dsseEnvelope": {"payload": base64.b64encode(payload).decode()}}


class FetchAttestationsTest(unittest.TestCase):
    def fake_run(self, bundles, api_stdout):
        def run(cmd, **kwargs):
            if cmd[:3] == ["gh", "attestation", "download"]:
                (Path(kwargs["cwd"]) / "sha256-x.jsonl").write_text(
                    "\n".join(json.dumps(b) for b in bundles) + "\n")
                return subprocess.CompletedProcess(cmd, 0, "", "")
            self.assertEqual(cmd[:2], ["gh", "api"])
            return subprocess.CompletedProcess(cmd, 0, api_stdout, "")
        return run

    def test_decode_attestation_payload(self):
        bundles = [statement(notes.PROVENANCE_TYPE, {"buildType": "x"}),
                   statement("https://spdx.dev/Document/v2.3", {"spdxVersion": "SPDX-2.3"})]
        api = json.dumps({"attestations": [{"id": 11}, {"id": 12}]})
        with mock.patch.object(registry.subprocess, "run", self.fake_run(bundles, api)):
            result = registry.fetch_attestations(
                "bill-of-materials/factory", "ghcr.io/bill-of-materials/ninjam", AMD)
        self.assertEqual([a.id for a in result], [11, 12])
        self.assertEqual(result[0].predicate_type, notes.PROVENANCE_TYPE)
        self.assertEqual(result[1].predicate, {"spdxVersion": "SPDX-2.3"})
        self.assertEqual(result[0].url, "https://github.com/bill-of-materials/factory/attestations/11")

    def test_id_none_when_counts_differ(self):
        bundles = [statement(notes.PROVENANCE_TYPE, {})]
        api = json.dumps({"attestations": []})
        with mock.patch.object(registry.subprocess, "run", self.fake_run(bundles, api)):
            result = registry.fetch_attestations("bill-of-materials/factory", "r/x", AMD)
        self.assertIsNone(result[0].id)
        self.assertIsNone(result[0].url)


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
    def test_release_notes_command_writes_notes_and_sboms(self):
        sbom = registry.Attestation(2, "https://spdx.dev/Document/v2.3", {"spdxVersion": "SPDX-2.3"})
        prov = registry.Attestation(1, notes.PROVENANCE_TYPE, {})

        def fetch(repo, ref, digest):
            return [prov] if digest == INDEX else [sbom]

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(registry, "digest_of", return_value=INDEX), \
                mock.patch.object(registry, "platform_digests", return_value=[("linux/arm/v7", ARM)]), \
                mock.patch.object(registry, "fetch_attestations", side_effect=fetch), \
                mock.patch.object(ci.versioning, "latest_tag", return_value="ninjam/v1.4.1"), \
                mock.patch.object(ci.versioning, "commits_since", return_value=[Commit(SHA, "fix: x", "")]):
            out = Path(tmp) / "notes.md"
            sboms = Path(tmp) / "sbom"
            rc = ci.main(["release-notes", "--image", "ninjam", "--version", "1.4.2", "--sha", SHA,
                          "--run-id", "9", "--out", str(out), "--sbom-dir", str(sboms)])
            self.assertEqual(rc, 0)
            self.assertIn("Changes since 1.4.1", out.read_text())
            self.assertEqual(
                json.loads((sboms / "ninjam-1.4.2-linux-arm-v7.spdx.json").read_text()),
                {"spdxVersion": "SPDX-2.3"})


if __name__ == "__main__":
    unittest.main()
