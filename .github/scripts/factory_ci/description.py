"""Package description: Dockerfile description label, application versions and a fixed suffix."""
import re
import shlex
from pathlib import Path

from factory_ci import appversion, images

LABEL_KEY = "org.opencontainers.image.description"
MAX_LENGTH = 512
SUFFIX_TEMPLATE = (
    " Signed multi-arch image. Release notes, changelog & verification: "
    "https://github.com/bill-of-materials/factory/releases?q={image}"
)


def _labels(dockerfile: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    text = re.sub(r"\\\s*\n", " ", dockerfile)
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.upper().startswith("LABEL "):
            continue
        for token in shlex.split(stripped[len("LABEL "):]):
            key, sep, value = token.partition("=")
            if sep:
                labels[key] = value
    return labels


def package_description(root: Path, image: str, sha: str | None = None) -> str:
    dockerfile = images.read_dockerfile(root, image, sha)
    label = _labels(dockerfile).get(LABEL_KEY)
    if label is None:
        raise ValueError(f"{image}: missing {LABEL_KEY} label")
    apps = appversion.summary(appversion.app_versions(image, dockerfile))
    suffix = (f" Application: {apps}." if apps else "") + SUFFIX_TEMPLATE.format(image=image)
    if len(label) + len(suffix) > MAX_LENGTH:
        label = label[: MAX_LENGTH - len(suffix) - 1] + "…"
    return label + suffix
