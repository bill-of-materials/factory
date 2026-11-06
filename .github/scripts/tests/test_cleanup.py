import unittest
from datetime import datetime, timedelta, timezone

from factory_ci.cleanup import IncompleteGraph, Version, select

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
OLD = NOW - timedelta(days=30)


def hexd(n: int) -> str:
    return f"{n:064x}"


def dig(n: int) -> str:
    return f"sha256:{hexd(n)}"


def att(n: int, suffix: str = "") -> str:
    return f"sha256-{hexd(n)}{suffix}"


def ver(i, n, tags=(), age=timedelta(days=30)):
    return Version(id=i, digest=dig(n), tags=tuple(tags), created_at=NOW - age)


class SelectTest(unittest.TestCase):
    def test_keeps_ten_most_recent_releases(self):
        versions = [
            ver(i, i, [f"1.{i}.0"], timedelta(days=100 - i)) for i in range(12)
        ]
        children = {v.digest: [] for v in versions}
        sel = select(versions, children, NOW)
        self.assertEqual({v.id for v in sel.delete}, {0, 1})
        self.assertEqual(sel.removed_releases, ["1.0.0", "1.1.0"])
        self.assertEqual(sel.keep, {dig(i) for i in range(2, 12)})

    def test_semver_order_not_creation_order(self):
        # 2.0.0 is the oldest by creation but the highest by semver; 1.9.0 and
        # 1.10.0 must compare as integers.
        versions = [
            ver(1, 1, ["2.0.0"], timedelta(days=90)),
            ver(2, 2, ["1.10.0"], timedelta(days=80)),
            ver(3, 3, ["1.9.0"], timedelta(days=70)),
        ]
        children = {v.digest: [] for v in versions}
        sel = select(versions, children, NOW, keep_releases=2)
        self.assertEqual([v.id for v in sel.delete], [3])
        self.assertEqual(sel.removed_releases, ["1.9.0"])

    def test_floating_tags_keep_their_targets(self):
        versions = [
            ver(1, 1, ["1.0.0"]),
            ver(2, 2, ["1.1.0"]),
            ver(3, 3, ["1", "1.1", "latest"]),
            ver(4, 4, ["2"]),
            ver(5, 5, ["2.0"]),
            ver(6, 6, ["latest"]),
            ver(7, 7, ["feature"]),
        ]
        children = {v.digest: [] for v in versions}
        sel = select(versions, children, NOW, keep_releases=1)
        self.assertEqual({v.id for v in sel.delete}, {1, 7})
        self.assertEqual(sel.removed_releases, ["1.0.0"])

    def test_children_and_signatures_of_kept_kept(self):
        idx, c1, c2, c3, ref, ref_child = 10, 11, 12, 13, 20, 21
        versions = [
            ver(1, idx, ["1.0.0"]),
            ver(2, c1),
            ver(3, c2),
            ver(4, c3),
            ver(5, 30, [att(idx, ".sig")]),
            ver(6, 31, [att(c1, ".sig")]),
            ver(7, ref, [att(idx)]),
            ver(8, ref_child),
            ver(9, 99),  # real orphan
        ]
        children = {
            dig(idx): [dig(c1), dig(c2), dig(c3)],
            dig(c1): [],
            dig(c2): [],
            dig(c3): [],
            dig(30): [],
            dig(31): [],
            dig(ref): [dig(ref_child)],
            dig(ref_child): [],
            dig(99): [],
        }
        sel = select(versions, children, NOW)
        self.assertEqual([v.id for v in sel.delete], [9])
        self.assertEqual(sel.removed_releases, [])

    def test_attachments_of_deleted_deleted(self):
        versions = [
            ver(1, 1, ["1.0.0"], timedelta(days=50)),
            ver(2, 2, ["1.1.0"], timedelta(days=40)),
            ver(3, 3, [att(1, ".sig")]),
            ver(4, 4, [att(1)]),
            ver(5, 5, [att(2, ".sig")]),
        ]
        children = {
            dig(1): [],
            dig(2): [],
            dig(3): [],
            dig(4): [dig(6)],
            dig(5): [],
            dig(6): [],
        }
        versions.append(ver(6, 6))
        sel = select(versions, children, NOW, keep_releases=1)
        self.assertEqual({v.id for v in sel.delete}, {1, 3, 4, 6})
        self.assertEqual(sel.removed_releases, ["1.0.0"])

    def test_legacy_sha_tagged_deleted(self):
        sha = "a" * 40
        versions = [
            ver(1, 1, ["1.0.0"]),
            ver(2, 2, [sha], timedelta(days=2)),
            ver(3, 3, [], timedelta(days=2)),
            ver(4, 4, [att(2, ".sig")], timedelta(days=2)),
        ]
        children = {dig(1): [], dig(2): [dig(3)], dig(3): [], dig(4): []}
        sel = select(versions, children, NOW)
        self.assertEqual({v.id for v in sel.delete}, {2, 3, 4})
        self.assertEqual(sel.removed_releases, [])

    def test_untagged_orphans_deleted(self):
        versions = [ver(1, 1, ["1.0.0"]), ver(2, 2), ver(3, 3)]
        children = {v.digest: [] for v in versions}
        sel = select(versions, children, NOW)
        self.assertEqual({v.id for v in sel.delete}, {2, 3})

    def test_recent_untagged_kept_24h(self):
        versions = [
            ver(1, 1, [], timedelta(hours=23, minutes=59)),
            ver(2, 2, [], timedelta(hours=24)),
            ver(3, 3, [], timedelta(hours=25)),
        ]
        children = {v.digest: [] for v in versions}
        sel = select(versions, children, NOW)
        self.assertEqual({v.id for v in sel.delete}, {2, 3})
        self.assertIn(dig(1), sel.keep)

    def test_delete_sorted_by_created_at(self):
        versions = [
            ver(1, 1, [], timedelta(days=3)),
            ver(2, 2, [], timedelta(days=9)),
            ver(3, 3, [], timedelta(days=5)),
        ]
        children = {v.digest: [] for v in versions}
        sel = select(versions, children, NOW)
        self.assertEqual([v.id for v in sel.delete], [2, 3, 1])

    def test_missing_children_raises(self):
        versions = [ver(1, 1, ["1.0.0"]), ver(2, 2)]
        with self.assertRaises(IncompleteGraph):
            select(versions, {dig(2): []}, NOW)

    def test_missing_children_of_closure_member_raises(self):
        versions = [ver(1, 1, ["1.0.0"]), ver(2, 2, [att(1, ".sig")])]
        with self.assertRaises(IncompleteGraph):
            select(versions, {dig(1): []}, NOW)

    def test_missing_children_of_deleted_ok(self):
        versions = [ver(1, 1, ["1.0.0"]), ver(2, 2)]
        sel = select(versions, {dig(1): []}, NOW)
        self.assertEqual([v.id for v in sel.delete], [2])


if __name__ == "__main__":
    unittest.main()
