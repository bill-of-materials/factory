"""Per-image semantic versioning derived from conventional commits."""
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

_BREAKING_SUBJECT = re.compile(r"^\w+(\([^)]*\))?!:")
_FEAT_SUBJECT = re.compile(r"^feat(\([^)]*\))?:")
_LEVELS = ("patch", "minor", "major")


@dataclass(frozen=True)
class Commit:
    sha: str
    subject: str
    body: str


@dataclass(frozen=True)
class VersionPlan:
    image: str
    version: str
    previous_version: str | None
    bump: str
    commits: list[Commit]


def bump_level(c: Commit) -> str:
    if _BREAKING_SUBJECT.match(c.subject) or "BREAKING CHANGE:" in c.body:
        return "major"
    if _FEAT_SUBJECT.match(c.subject):
        return "minor"
    return "patch"


def bump(version: str, level: str) -> str:
    major, minor, patch = (int(p) for p in version.split("."))
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True,
    ).stdout


def latest_tag(root: Path, image: str) -> str | None:
    pattern = re.compile(rf"^{re.escape(image)}/v(\d+)\.(\d+)\.(\d+)$")
    found = []
    for name in _git(root, "tag", "--list", f"{image}/v*").split():
        m = pattern.match(name)
        if m:
            found.append((tuple(int(g) for g in m.groups()), name))
    return max(found)[1] if found else None


def commits_since(root: Path, image: str, tag: str | None, head: str = "HEAD") -> list[Commit]:
    rev = f"refs/tags/{tag}..{head}" if tag else head
    out = _git(
        root, "log", "--no-merges", "-z", "--format=%H%x1f%s%x1f%b", rev,
        "--", f"{image}/", f":(exclude,glob){image}/**/*.md",
    )
    commits = []
    for record in out.split("\0"):
        if not record.strip():
            continue
        sha, subject, body = record.strip("\n").split("\x1f", 2)
        commits.append(Commit(sha, subject, body.strip()))
    return commits


def next_version(root: Path, image: str, head: str = "HEAD") -> VersionPlan | None:
    tag = latest_tag(root, image)
    commits = commits_since(root, image, tag, head)
    if not commits:
        return None
    if tag is None:
        return VersionPlan(image, "1.0.0", None, "initial", commits)
    previous = tag.rsplit("/v", 1)[1]
    level = max((bump_level(c) for c in commits), key=_LEVELS.index)
    return VersionPlan(image, bump(previous, level), previous, level, commits)
