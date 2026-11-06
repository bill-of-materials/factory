"""Registry lookups through crane."""
import base64
import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

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


@dataclass(frozen=True)
class Attestation:
    id: int | None
    predicate_type: str
    predicate: dict
    repo: str = "bill-of-materials/factory"

    @property
    def url(self) -> str | None:
        if self.id is None:
            return None
        return f"https://github.com/{self.repo}/attestations/{self.id}"


def _gh(*args: str, cwd: str | None = None) -> str:
    result = subprocess.run(["gh", *args], capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def fetch_attestations(repo: str, ref: str, digest: str) -> list[Attestation]:
    """Attestations of ref@digest: statements from the downloaded bundles, ids from the REST API.

    Ids are matched to bundles by order, only when both lists have the same length.
    """
    with tempfile.TemporaryDirectory() as tmp:
        _gh("attestation", "download", f"oci://{ref}@{digest}", "-R", repo, cwd=tmp)
        lines = [line for f in sorted(Path(tmp).glob("*.jsonl"))
                 for line in f.read_text().splitlines() if line.strip()]
    statements = []
    for line in lines:
        bundle = json.loads(line)
        bundle = bundle.get("bundle", bundle)
        statements.append(json.loads(base64.b64decode(bundle["dsseEnvelope"]["payload"])))
    api = json.loads(_gh("api", f"repos/{repo}/attestations/{digest}"))
    ids = [a.get("id") for a in api.get("attestations", [])]
    if len(ids) != len(statements):
        ids = [None] * len(statements)
    return [Attestation(i, s["predicateType"], s.get("predicate", {}), repo)
            for i, s in zip(ids, statements)]


def platform_digests(index_ref: str) -> list[tuple[str, str]]:
    result = _crane("manifest", index_ref)
    if result.returncode != 0:
        raise RuntimeError(f"crane manifest {index_ref} failed: {result.stderr.strip()}")
    found = []
    for m in json.loads(result.stdout).get("manifests", []):
        p = m.get("platform")
        if not p or "unknown" in (p.get("os"), p.get("architecture")):
            continue
        name = f"{p['os']}/{p['architecture']}" + (f"/{p['variant']}" if p.get("variant") else "")
        found.append((name, m["digest"]))
    return found
