import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

import ci
from factory_ci import registry
from factory_ci.cleanup import Version

OLD = datetime(2020, 1, 1, tzinfo=timezone.utc)


def digest(n: int) -> str:
    return f"sha256:{n:064x}"


def version(id_: int, n: int, *tags: str) -> Version:
    return Version(id_, digest(n), tuple(tags), OLD)


class CleanupCliTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "alpine3").mkdir()
        (self.root / "alpine3/Dockerfile").write_text("FROM scratch\n")
        # 12 releases, one image manifest each (leaf); oldest two get removed
        self.versions = [version(i, i, f"1.0.{i}") for i in range(1, 13)]

    def run_cli(self, *extra, versions=None, children=None, subjects=None, delete_side_effect=None):
        def manifest_links(ref):
            digest_ = ref.split("@")[1]
            return registry.ManifestLinks(
                children(ref) if children is not None else [],
                (subjects or {}).get(digest_))

        with mock.patch.object(registry, "list_versions", return_value=versions or self.versions) as lv, \
                mock.patch.object(registry, "manifest_links", side_effect=manifest_links) as mc, \
                mock.patch.object(registry, "delete_version", side_effect=delete_side_effect) as dv, \
                mock.patch.object(registry, "mark_release_removed") as mr:
            out = io.StringIO()
            with redirect_stdout(out):
                code = ci.main(["--root", str(self.root), "cleanup", "--org", "bill-of-materials", *extra])
        return code, out.getvalue(), lv, mc, dv, mr

    def test_cleanup_cli_dry_run_deletes_nothing(self):
        code, out, _, _, dv, mr = self.run_cli("--dry-run")
        self.assertEqual(code, 0)
        dv.assert_not_called()
        mr.assert_not_called()
        self.assertIn("DRY-RUN would delete 1 ", out)
        self.assertIn("DRY-RUN would delete 2 ", out)
        self.assertIn("kept 10", out)

    def test_cleanup_cli_real_run_deletes_selected_and_marks_releases(self):
        code, out, _, _, dv, mr = self.run_cli()
        self.assertEqual(code, 0)
        self.assertEqual([c.args for c in dv.call_args_list],
                         [("bill-of-materials", "alpine3", 1), ("bill-of-materials", "alpine3", 2)])
        self.assertEqual({c.args[1:3] for c in mr.call_args_list},
                         {("alpine3", "1.0.1"), ("alpine3", "1.0.2")})
        self.assertNotIn("DRY-RUN", out)

    def test_single_image_option_limits_scope(self):
        (self.root / "other").mkdir()
        (self.root / "other/Dockerfile").write_text("FROM scratch\n")
        _, _, lv, _, _, _ = self.run_cli("--image", "other")
        lv.assert_called_once_with("bill-of-materials", "other")

    def test_listed_child_manifests_are_fetched_too(self):
        index = version(1, 100, "2.0.0")
        child = digest(101)
        seen = []

        def children(ref):
            seen.append(ref.split("@")[1])
            return [child] if ref.endswith(index.digest) else []

        code, *_ = self.run_cli("--dry-run", versions=[index], children=children)
        self.assertEqual(code, 0)
        self.assertIn(child, seen)

    def test_referrer_of_kept_release_is_kept(self):
        referrer = version(13, 13)  # untagged, subject = newest release
        orphan = version(14, 14)  # untagged, subject = a removed release
        subjects = {referrer.digest: digest(12), orphan.digest: digest(1)}
        code, out, _, _, dv, _ = self.run_cli(versions=[*self.versions, referrer, orphan],
                                              subjects=subjects)
        self.assertEqual(code, 0)
        self.assertEqual(sorted(c.args[2] for c in dv.call_args_list), [1, 2, 14])

    def test_manifest_error_deletes_nothing_and_exits_1(self):
        def children(ref):
            raise RuntimeError("boom")

        code, _, _, _, dv, mr = self.run_cli(children=children)
        self.assertEqual(code, 1)
        dv.assert_not_called()
        mr.assert_not_called()

    def test_incomplete_graph_deletes_nothing_and_continues_with_next_image(self):
        (self.root / "zeta").mkdir()
        (self.root / "zeta/Dockerfile").write_text("FROM scratch\n")
        # our own closure never leaves gaps, so simulate by making select raise for the first image
        from factory_ci import cleanup
        calls = []
        real_select = cleanup.select

        def fake_select(versions, children, now, keep_releases=10, subjects=None):
            calls.append(1)
            if len(calls) == 1:
                raise cleanup.IncompleteGraph("sha256:x")
            return real_select(versions, children, now, keep_releases, subjects=subjects)

        with mock.patch.object(cleanup, "select", side_effect=fake_select):
            code, _, lv, _, dv, _ = self.run_cli()
        self.assertEqual(code, 1)
        self.assertEqual(lv.call_count, 2)
        self.assertEqual([c.args[1] for c in dv.call_args_list], ["zeta", "zeta"])

    def test_delete_failure_exits_1(self):
        code, *_ = self.run_cli(delete_side_effect=RuntimeError("denied"))
        self.assertEqual(code, 1)


class RegistryIoTest(unittest.TestCase):
    def test_list_versions_flattens_pages(self):
        pages = [[{"id": 1, "name": "sha256:a", "created_at": "2026-01-02T03:04:05Z",
                   "metadata": {"container": {"tags": ["1.0.0", "latest"]}}}],
                 [{"id": 2, "name": "sha256:b", "created_at": "2026-01-01T00:00:00Z",
                   "metadata": {"container": {"tags": []}}}]]
        with mock.patch.object(registry, "_gh", return_value=json.dumps(pages)) as gh:
            result = registry.list_versions("org", "img")
        self.assertEqual(gh.call_args.args, ("api", "--paginate", "--slurp",
                                             "/orgs/org/packages/container/img/versions"))
        self.assertEqual([v.id for v in result], [1, 2])
        self.assertEqual(result[0].tags, ("1.0.0", "latest"))
        self.assertEqual(result[0].created_at, datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc))

    def test_list_versions_404_is_empty_and_other_errors_raise(self):
        with mock.patch.object(registry, "_gh", side_effect=RuntimeError("gh: Not Found (HTTP 404)")):
            self.assertEqual(registry.list_versions("org", "img"), [])
        with mock.patch.object(registry, "_gh", side_effect=RuntimeError("gh: HTTP 500")):
            with self.assertRaises(RuntimeError):
                registry.list_versions("org", "img")

    def _crane(self, stdout="", returncode=0, stderr=""):
        return mock.patch.object(registry, "_crane", return_value=subprocess.CompletedProcess(
            [], returncode, stdout, stderr))

    def test_manifest_links(self):
        index = json.dumps({"manifests": [{"digest": "sha256:a"}, {"digest": "sha256:b"}]})
        with self._crane(index):
            self.assertEqual(registry.manifest_links("r@sha256:x"),
                             registry.ManifestLinks(["sha256:a", "sha256:b"], None))
        with self._crane(json.dumps({"layers": []})):
            self.assertEqual(registry.manifest_links("r@sha256:x"), registry.ManifestLinks([], None))
        referrer = json.dumps({"layers": [], "subject": {"digest": "sha256:s"}})
        with self._crane(referrer):
            self.assertEqual(registry.manifest_links("r@sha256:x"),
                             registry.ManifestLinks([], "sha256:s"))
        with self._crane(returncode=1, stderr="denied"):
            with self.assertRaises(RuntimeError):
                registry.manifest_links("r@sha256:x")

    def test_delete_version(self):
        with mock.patch.object(registry, "_gh") as gh:
            registry.delete_version("org", "img", 7)
        gh.assert_called_once_with("api", "-X", "DELETE", "/orgs/org/packages/container/img/versions/7")

    def test_mark_release_removed_appends_once(self):
        calls = []

        def gh(*args):
            calls.append(args)
            if args[:2] == ("release", "view"):
                return "Notes\n"
            if args[:2] == ("release", "edit"):
                self.edited = Path(args[args.index("--notes-file") + 1]).read_text()
            return ""

        with mock.patch.object(registry, "_gh", side_effect=gh):
            registry.mark_release_removed("o/r", "img", "1.0.1", date(2026, 11, 6))
        self.assertEqual(self.edited, "Notes\n\nThis image version was removed from the registry "
                                      "on 2026-11-06 (retention: last 10 releases).\n")
        self.assertEqual(calls[1][:3], ("release", "edit", "img/v1.0.1"))

    def test_mark_release_removed_skips_when_present_or_missing(self):
        already = ("Notes\n\nThis image version was removed from the registry "
                   "on 2026-10-01 (retention: last 10 releases).\n")
        calls = []

        def gh(*args):
            calls.append(args[:2])
            return already

        with mock.patch.object(registry, "_gh", side_effect=gh):
            registry.mark_release_removed("o/r", "img", "1.0.1", date(2026, 11, 6))
        self.assertEqual(calls, [("release", "view")])

        with mock.patch.object(registry, "_gh", side_effect=RuntimeError("release not found")) as gh:
            registry.mark_release_removed("o/r", "img", "1.0.1", date(2026, 11, 6))
        self.assertEqual(gh.call_count, 1)


if __name__ == "__main__":
    unittest.main()
