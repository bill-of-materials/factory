import tempfile
import unittest

from factory_ci.images import (
    DEFAULT_PLATFORMS, build_matrix, changed_images, list_images,
    platform_slug, read_platforms, runner_for,
)
from tests.helpers import GitRepo


class ImagesTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = GitRepo(tmp.name)
        self.root = self.repo.root
        for image in ("alpine3", "ninjam", "snapcast"):
            self.repo.write(f"{image}/Dockerfile", "FROM scratch\n")
        self.repo.write("docs/index.md", "x\n")
        self.repo.write(".github/ci.txt", "x\n")
        self.repo.commit("chore: init")

    def test_default_platforms_when_no_file(self):
        self.assertEqual(read_platforms(self.root, "alpine3"), ["linux/amd64", "linux/arm64", "linux/arm/v7"])
        self.assertEqual(DEFAULT_PLATFORMS, read_platforms(self.root, "alpine3"))

    def test_platforms_file_whitespace_ignored(self):
        self.repo.write("ninjam/platforms.txt", " linux/amd64 ,\nlinux/arm64\n")
        self.assertEqual(read_platforms(self.root, "ninjam"), ["linux/amd64", "linux/arm64"])

    def test_runner_mapping(self):
        self.assertEqual(runner_for("linux/amd64"), {"runner": "ubuntu-24.04", "qemu": False, "cst_arch": "amd64"})
        self.assertEqual(runner_for("linux/arm64"), {"runner": "ubuntu-24.04-arm", "qemu": False, "cst_arch": "arm64"})
        self.assertEqual(runner_for("linux/arm/v7"), {"runner": "ubuntu-24.04", "qemu": True, "cst_arch": "amd64"})

    def test_unknown_platform_falls_back_to_qemu(self):
        self.assertEqual(runner_for("linux/riscv64"), {"runner": "ubuntu-24.04", "qemu": True, "cst_arch": "amd64"})

    def test_platform_slug(self):
        self.assertEqual(platform_slug("linux/arm/v7"), "linux-arm-v7")

    def test_build_matrix_one_entry_per_platform(self):
        matrix = build_matrix(self.root, ["alpine3"])
        self.assertEqual(len(matrix), 3)
        v7 = [e for e in matrix if e["slug"] == "linux-arm-v7"][0]
        self.assertEqual(v7, {
            "image": "alpine3", "platform": "linux/arm/v7", "slug": "linux-arm-v7",
            "runner": "ubuntu-24.04", "qemu": True, "cst_arch": "amd64",
        })

    def test_list_images_ignores_dirs_without_dockerfile(self):
        self.assertEqual(list_images(self.root), ["alpine3", "ninjam", "snapcast"])

    def test_changed_images_ignores_markdown_only(self):
        base = self.repo.git("rev-parse", "HEAD")
        self.repo.write("ninjam/README.md", "doc\n")
        self.repo.commit("doc: readme")
        self.assertEqual(changed_images(self.root, base, "HEAD"), [])

    def test_changed_images_detects_dockerfile_change(self):
        base = self.repo.git("rev-parse", "HEAD")
        self.repo.write("snapcast/Dockerfile", "FROM alpine\n")
        self.repo.write("docs/index.md", "y\n")
        self.repo.commit("fix: snapcast")
        self.assertEqual(changed_images(self.root, base, "HEAD"), ["snapcast"])

    def test_changed_images_uses_merge_base(self):
        self.repo.checkout("feature", create=True)
        self.repo.write("ninjam/Dockerfile", "FROM alpine\n")
        self.repo.commit("fix: ninjam")
        self.repo.checkout("main")
        self.repo.write("snapcast/Dockerfile", "FROM alpine\n")
        self.repo.commit("fix: snapcast")
        self.assertEqual(changed_images(self.root, "main", "feature"), ["ninjam"])


if __name__ == "__main__":
    unittest.main()
