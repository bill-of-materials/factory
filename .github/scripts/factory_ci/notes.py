"""Per-image release notes rendered from registry and GitHub state."""
import re
from dataclasses import dataclass, field

from factory_ci.appversion import AppVersion, source_link
from factory_ci.registry import REGISTRY
from factory_ci.versioning import Commit

PROVENANCE_TYPE = "https://slsa.dev/provenance/v1"
SBOM_TYPE_PREFIX = "https://spdx.dev/Document"

_PR = re.compile(r"\(#(\d+)\)")
_IDENTITY = r"^https://github.com/{repo}/\.github/workflows/release\.yml@refs/heads/main$"
_OIDC_ISSUER = "https://token.actions.githubusercontent.com"


@dataclass
class ReleaseContext:
    image: str
    version: str
    previous_version: str | None
    index_digest: str
    platforms: list[tuple[str, str]]
    commits: list[Commit]
    repo: str
    server_url: str
    sha: str
    run_id: str
    provenance_url: str | None = None
    sbom_urls: dict[str, str] = field(default_factory=dict)
    apps: list[AppVersion] = field(default_factory=list)


def _commit_line(ctx: ReleaseContext, c: Commit) -> str:
    short = c.sha[:7]
    refs = [f"[{short}]({ctx.server_url}/{ctx.repo}/commit/{c.sha})"]
    for n in _PR.findall(c.subject):
        refs.append(f"[#{n}]({ctx.server_url}/{ctx.repo}/pull/{n})")
    return f"- {c.subject} ({', '.join(refs)})"


def _app_line(v: AppVersion) -> str:
    link = source_link(v)
    shown = f"[`{v.display}`]({link})" if link else f"`{v.display}`"
    return f"- {v.name} {shown}"


def render_release_notes(ctx: ReleaseContext) -> str:
    ref = f"{REGISTRY}/{ctx.image}"
    pinned = f"{ref}@{ctx.index_digest}"
    heading = (f"Changes since {ctx.previous_version}" if ctx.previous_version
               else "Changes (first release)")
    lines = [
        f"## {ctx.image} {ctx.version}", "",
        "**Pull**", "",
        "```bash",
        f"docker pull {ref}:{ctx.version}",
        f"docker pull {pinned}",
        "```", "",
        *(["**Application**", "", *[_app_line(v) for v in ctx.apps], ""] if ctx.apps else []),
        "**Architectures**", "",
        *[f"- `{platform}`: `{digest}`" for platform, digest in ctx.platforms], "",
        f"**{heading}**", "",
        *([_commit_line(ctx, c) for c in ctx.commits] or ["- No changes recorded"]), "",
        "**Build & supply chain**", "",
        f"- Commit: {ctx.server_url}/{ctx.repo}/commit/{ctx.sha}",
        f"- Pipeline run: {ctx.server_url}/{ctx.repo}/actions/runs/{ctx.run_id}",
    ]
    if ctx.provenance_url:
        lines.append(f"- Provenance attestation: {ctx.provenance_url}")
    for platform, _ in ctx.platforms:
        if platform in ctx.sbom_urls:
            lines.append(f"- SBOM attestation ({platform}): {ctx.sbom_urls[platform]}")
    if not ctx.provenance_url and not ctx.sbom_urls:
        lines.append(f"- Attestations: {ctx.server_url}/{ctx.repo}/attestations")
    lines += [
        "- SBOM files (SPDX JSON, one per architecture): attached to this release",
        "- Signed with cosign (keyless, GitHub OIDC), index and every architecture",
        "", "**Verify**", "",
        "```bash",
        f"cosign verify {pinned} \\",
        f"  --certificate-identity-regexp '{_IDENTITY.format(repo=ctx.repo)}' \\",
        f"  --certificate-oidc-issuer {_OIDC_ISSUER}",
        f"gh attestation verify oci://{pinned} -R {ctx.repo}",
        "```", "",
    ]
    return "\n".join(lines)
