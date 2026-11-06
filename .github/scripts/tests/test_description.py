import tempfile
import unittest
from pathlib import Path

from factory_ci import description, images

REPO_ROOT = Path(__file__).resolve().parents[3]
KEY = "org.opencontainers.image.description"


def make_root(dockerfile: str, image: str = "demo"):
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    (root / image).mkdir()
    (root / image / "Dockerfile").write_text(dockerfile)
    return root, tmp


class DescriptionTest(unittest.TestCase):
    def fixture(self, dockerfile):
        root, tmp = make_root(dockerfile)
        self.addCleanup(tmp.cleanup)
        return root

    def test_description_appends_suffix(self):
        root = self.fixture(f'FROM x\nLABEL {KEY}="Hello world."\n')
        self.assertEqual(
            description.package_description(root, "demo"),
            "Hello world. Signed multi-arch image. Release notes, changelog & "
            "verification: https://github.com/bill-of-materials/factory/releases?q=demo",
        )

    def test_multiline_label_block(self):
        root = self.fixture(
            f'FROM x\nLABEL a="1" \\\n      {KEY}="Multi line." \\\n      b="2"\n'
        )
        self.assertTrue(
            description.package_description(root, "demo").startswith("Multi line. Signed")
        )

    def test_missing_label_raises(self):
        root = self.fixture('FROM x\nLABEL a="1"\n')
        with self.assertRaisesRegex(ValueError, "demo: missing org.opencontainers.image.description label"):
            description.package_description(root, "demo")

    def test_long_label_truncated_to_512(self):
        root = self.fixture(f'FROM x\nLABEL {KEY}="{"é" * 600}"\n')
        result = description.package_description(root, "demo")
        suffix = description.SUFFIX_TEMPLATE.format(image="demo")
        self.assertEqual(len(result), 512)
        self.assertTrue(result.endswith(suffix))
        self.assertIn("…" + suffix, result)

    def test_all_repo_images_have_description(self):
        for image in images.list_images(REPO_ROOT):
            with self.subTest(image=image):
                self.assertLessEqual(len(description.package_description(REPO_ROOT, image)), 512)


if __name__ == "__main__":
    unittest.main()
