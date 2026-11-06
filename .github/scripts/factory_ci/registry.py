"""Registry lookups through crane."""
import json
import subprocess

REGISTRY = "ghcr.io/bill-of-materials"
REVISION_ANNOTATION = "org.opencontainers.image.revision"
UNKNOWN_REVISION = "<unknown>"
_ABSENT_MARKERS = ("manifest_unknown", "name_unknown", "not found")


def _crane(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["crane", *args], capture_output=True, text=True)


def index_revision(ref: str) -> str | None:
    """Revision annotation of the index at ref.

    None only when the ref is absent; UNKNOWN_REVISION when it exists without the annotation.
    """
    result = _crane("manifest", ref)
    if result.returncode != 0:
        if any(m in result.stderr.lower() for m in _ABSENT_MARKERS):
            return None
        raise RuntimeError(f"crane manifest {ref} failed: {result.stderr.strip()}")
    return json.loads(result.stdout).get("annotations", {}).get(REVISION_ANNOTATION, UNKNOWN_REVISION)


def digest_of(ref: str) -> str:
    result = _crane("digest", ref)
    if result.returncode != 0:
        raise RuntimeError(f"crane digest {ref} failed: {result.stderr.strip()}")
    return result.stdout.strip()
