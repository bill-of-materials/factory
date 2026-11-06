"""Registry retention: decide which GHCR package versions to delete.

Pure logic, no I/O. When in doubt, keep.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

RELEASE_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
FLOATING_RE = re.compile(r"^(?:\d+|\d+\.\d+|latest)$")
ATTACHMENT_RE = re.compile(r"^sha256-([0-9a-f]{64})(?:\..+)?$")
RECENT = timedelta(hours=24)


@dataclass(frozen=True)
class Version:
    id: int
    digest: str
    tags: tuple[str, ...]
    created_at: datetime


class IncompleteGraph(Exception):
    """A kept digest has no entry in the children map."""


@dataclass
class Selection:
    keep: set[str]
    delete: list[Version]
    removed_releases: list[str]


def _semver(tag: str) -> tuple[int, int, int] | None:
    m = RELEASE_RE.match(tag)
    return tuple(map(int, m.groups())) if m else None


def _attached_to(tag: str) -> str | None:
    m = ATTACHMENT_RE.match(tag)
    return f"sha256:{m.group(1)}" if m else None


def select(
    versions: list[Version],
    children: dict[str, list[str]],
    now: datetime,
    keep_releases: int = 10,
    subjects: dict[str, str] | None = None,
) -> Selection:
    """Select versions to delete.

    children maps a digest to the digests its manifest references; subjects maps a
    referrer digest to its OCI `subject` digest. Children, `sha256-<hex>*` attachment
    tags and referrers of a kept digest are kept too (closure).
    """
    releases = sorted(
        ((_semver(t), v.digest) for v in versions for t in v.tags if _semver(t)),
        reverse=True,
    )
    keep = {digest for _, digest in releases[:keep_releases]}
    keep |= {
        v.digest for v in versions if any(FLOATING_RE.match(t) for t in v.tags)
    }
    keep |= {v.digest for v in versions if now - v.created_at < RECENT}

    attachments: dict[str, list[str]] = {}
    for v in versions:
        for t in v.tags:
            target = _attached_to(t)
            if target:
                attachments.setdefault(target, []).append(v.digest)
    for referrer, subject in (subjects or {}).items():
        attachments.setdefault(subject, []).append(referrer)

    pending = list(keep)
    while pending:
        digest = pending.pop()
        if digest not in children:
            raise IncompleteGraph(digest)
        for nxt in [*children[digest], *attachments.get(digest, [])]:
            if nxt not in keep:
                keep.add(nxt)
                pending.append(nxt)

    delete = sorted(
        (v for v in versions if v.digest not in keep), key=lambda v: v.created_at
    )
    removed = sorted(
        {t for v in delete for t in v.tags if _semver(t)}, key=_semver
    )
    return Selection(keep=keep, delete=delete, removed_releases=removed)
