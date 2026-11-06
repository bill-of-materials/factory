"""Image discovery, platform-to-runner mapping and build matrix."""
import subprocess
from pathlib import Path

DEFAULT_PLATFORMS = ["linux/amd64", "linux/arm64", "linux/arm/v7"]

_NATIVE_RUNNERS = {
    "linux/amd64": {"runner": "ubuntu-24.04", "qemu": False, "cst_arch": "amd64"},
    "linux/arm64": {"runner": "ubuntu-24.04-arm", "qemu": False, "cst_arch": "arm64"},
}
_QEMU_RUNNER = {"runner": "ubuntu-24.04", "qemu": True, "cst_arch": "amd64"}


def list_images(root: Path) -> list[str]:
    return sorted(
        p.name for p in Path(root).iterdir()
        if p.is_dir() and (p / "Dockerfile").is_file()
    )


def read_platforms(root: Path, image: str) -> list[str]:
    path = Path(root) / image / "platforms.txt"
    if not path.is_file():
        return list(DEFAULT_PLATFORMS)
    platforms = [p.strip() for p in path.read_text().replace("\n", ",").split(",")]
    return [p for p in platforms if p]


def runner_for(platform: str) -> dict:
    return dict(_NATIVE_RUNNERS.get(platform, _QEMU_RUNNER))


def platform_slug(platform: str) -> str:
    return platform.replace("/", "-")


def build_matrix(root: Path, images: list[str]) -> list[dict]:
    return [
        {"image": image, "platform": platform, "slug": platform_slug(platform),
         **runner_for(platform)}
        for image in images
        for platform in read_platforms(root, image)
    ]


def is_release_relevant(path: str) -> bool:
    return not path.endswith(".md")


def changed_images(root: Path, base: str, head: str) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head}"],
        cwd=root, capture_output=True, text=True, check=True,
    ).stdout
    known = set(list_images(root))
    found = {
        path.split("/", 1)[0]
        for path in out.splitlines()
        if "/" in path and is_release_relevant(path)
    }
    return sorted(found & known)
