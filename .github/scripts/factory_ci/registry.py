"""Registry lookups through crane."""
import base64
import json
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from factory_ci.cleanup import Version

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
    predicate_type: str
    predicate: dict


def _gh(*args: str, cwd: str | None = None) -> str:
    result = subprocess.run(["gh", *args], capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def fetch_attestations(repo: str, ref: str, digest: str) -> list[Attestation]:
    """Predicates of the attestations of ref@digest, read from the downloaded Sigstore bundles."""
    with tempfile.TemporaryDirectory() as tmp:
        _gh("attestation", "download", f"oci://{ref}@{digest}", "-R", repo, cwd=tmp)
        lines = [line for f in sorted(Path(tmp).glob("*.jsonl"))
                 for line in f.read_text().splitlines() if line.strip()]
    found = []
    for line in lines:
        bundle = json.loads(line)
        bundle = bundle.get("bundle", bundle)
        statement = json.loads(base64.b64decode(bundle["dsseEnvelope"]["payload"]))
        found.append(Attestation(statement["predicateType"], statement.get("predicate", {})))
    return found


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


def list_versions(org: str, image: str) -> list[Version]:
    """Every version of the GHCR package; [] when the package does not exist."""
    try:
        out = _gh("api", "--paginate", "--slurp", f"/orgs/{org}/packages/container/{image}/versions")
    except RuntimeError as exc:
        if "404" in str(exc) or "not found" in str(exc).lower():
            return []
        raise
    return [
        Version(
            id=item["id"],
            digest=item["name"],
            tags=tuple(item.get("metadata", {}).get("container", {}).get("tags", [])),
            created_at=datetime.fromisoformat(item["created_at"].replace("Z", "+00:00")),
        )
        for page in json.loads(out)
        for item in page
    ]


@dataclass(frozen=True)
class ManifestLinks:
    children: list[str]  # index entries; [] for an image manifest
    subject: str | None  # OCI referrer `subject` digest


def manifest_links(ref: str) -> ManifestLinks:
    """Digests the manifest at ref points to: its index entries and its OCI subject."""
    result = _crane("manifest", ref)
    if result.returncode != 0:
        raise RuntimeError(f"crane manifest {ref} failed: {result.stderr.strip()}")
    manifest = json.loads(result.stdout)
    return ManifestLinks(
        [m["digest"] for m in manifest.get("manifests", [])],
        manifest.get("subject", {}).get("digest"),
    )


def delete_version(org: str, image: str, version_id: int) -> None:
    _gh("api", "-X", "DELETE", f"/orgs/{org}/packages/container/{image}/versions/{version_id}")


REMOVAL_NOTE_PREFIX = "This image version was removed from the registry on "


def mark_release_removed(repo: str, image: str, version: str, today: date) -> None:
    """Append the removal note to the release notes once; no-op if the release is missing."""
    tag = f"{image}/v{version}"
    try:
        body = _gh("release", "view", tag, "-R", repo, "--json", "body", "--jq", ".body")
    except RuntimeError as exc:
        if "not found" in str(exc).lower():
            return
        raise
    if REMOVAL_NOTE_PREFIX in body:
        return
    note = f"{REMOVAL_NOTE_PREFIX}{today.isoformat()} (retention: last 10 releases)."
    with tempfile.TemporaryDirectory() as tmp:
        notes_file = Path(tmp) / "notes.md"
        notes_file.write_text(f"{body.rstrip()}\n\n{note}\n")
        _gh("release", "edit", tag, "-R", repo, "--notes-file", str(notes_file))
