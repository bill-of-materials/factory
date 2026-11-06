"""Package description: Dockerfile description label plus a fixed suffix."""
import re
import shlex
from pathlib import Path

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


def package_description(root: Path, image: str) -> str:
    labels = _labels((Path(root) / image / "Dockerfile").read_text())
    label = labels.get(LABEL_KEY)
    if label is None:
        raise ValueError(f"{image}: missing {LABEL_KEY} label")
    suffix = SUFFIX_TEMPLATE.format(image=image)
    if len(label) + len(suffix) > MAX_LENGTH:
        label = label[: MAX_LENGTH - len(suffix) - 1] + "…"
    return label + suffix
