#!/usr/bin/env python3
"""CI entry point: one argparse subcommand per workflow decision."""
import argparse
import json
import os
import sys
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from factory_ci import description, images, notes, registry, versioning  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]

NONE_ENTRY = {
    "image": "__none__", "platform": "__none__", "slug": "none",
    "runner": "ubuntu-24.04", "qemu": False, "cst_arch": "amd64",
}
RELEASE_NONE_ENTRY = {**NONE_ENTRY, "version": "__none__"}


def release_plan(root: Path, head_sha: str,
                 revision_lookup: Callable[[str], str | None]) -> list[dict]:
    """Plan releases for images with changes; resume what is already published from head_sha."""
    plans = []
    for image in images.list_images(root):
        plan = versioning.next_version(root, image, head_sha)
        if plan is None:
            continue
        published = revision_lookup(f"{registry.REGISTRY}/{image}:{plan.version}")
        if published is None:
            mode = "build"
        elif published == head_sha:
            mode = "resume"
        else:
            raise RuntimeError(f"{image}:{plan.version} already published from {published}")
        plans.append({
            "image": image, "version": plan.version,
            "previous_version": plan.previous_version, "bump": plan.bump, "mode": mode,
        })
    return plans


def cmd_pr_matrix(args) -> int:
    changed = images.changed_images(args.root, args.base, args.head)
    matrix = images.build_matrix(args.root, changed)
    print(f"matrix={json.dumps(matrix or [NONE_ENTRY], separators=(',', ':'))}")
    print(f"has_images={'true' if matrix else 'false'}")
    return 0


def cmd_release_plan(args) -> int:
    plans = release_plan(args.root, args.head_sha, registry.index_revision)
    versions = {p["image"]: p["version"] for p in plans if p["mode"] == "build"}
    matrix = [{**entry, "version": versions[entry["image"]]}
              for entry in images.build_matrix(args.root, list(versions))]
    print(f"plans={json.dumps(plans, separators=(',', ':'))}")
    print(f"matrix={json.dumps(matrix or [RELEASE_NONE_ENTRY], separators=(',', ':'))}")
    print(f"has_builds={'true' if matrix else 'false'}")
    print(f"has_releases={'true' if plans else 'false'}")
    return 0


def cmd_description(args) -> int:
    print(description.package_description(args.root, args.image))
    return 0


def cmd_release_notes(args) -> int:
    repo = os.environ.get("GITHUB_REPOSITORY", "bill-of-materials/factory")
    server_url = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    ref = f"{registry.REGISTRY}/{args.image}"
    index_digest = registry.digest_of(f"{ref}:{args.version}")
    platforms = registry.platform_digests(f"{ref}@{index_digest}")
    prev_tag = versioning.latest_tag(args.root, args.image)
    previous = prev_tag.split("/v", 1)[1] if prev_tag else None
    commits = versioning.commits_since(args.root, args.image, prev_tag, head=args.sha)

    sbom_urls = dict(item.split("=", 1) for item in args.sbom_url)
    args.sbom_dir.mkdir(parents=True, exist_ok=True)
    for platform, digest in platforms:
        sboms = [a for a in registry.fetch_attestations(repo, ref, digest)
                 if a.predicate_type.startswith(notes.SBOM_TYPE_PREFIX)]
        if not sboms:
            continue
        name = f"{args.image}-{args.version}-{images.platform_slug(platform)}.spdx.json"
        (args.sbom_dir / name).write_text(json.dumps(sboms[0].predicate))

    ctx = notes.ReleaseContext(
        image=args.image, version=args.version, previous_version=previous,
        index_digest=index_digest, platforms=platforms, commits=commits, repo=repo,
        server_url=server_url, sha=args.sha, run_id=args.run_id,
        provenance_url=args.provenance_url, sbom_urls=sbom_urls,
    )
    args.out.write_text(notes.render_release_notes(ctx))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ci.py")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("pr-matrix", help="build matrix for images changed in a PR")
    p.add_argument("--base", required=True)
    p.add_argument("--head", required=True)
    p.set_defaults(func=cmd_pr_matrix)

    p = sub.add_parser("release-plan", help="plan releases and detect resumable ones")
    p.add_argument("--head-sha", required=True)
    p.set_defaults(func=cmd_release_plan)

    p = sub.add_parser("description", help="print the package description for an image")
    p.add_argument("--image", required=True)
    p.set_defaults(func=cmd_description)

    p = sub.add_parser("release-notes", help="render release notes and extract SBOM files")
    p.add_argument("--image", required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--sha", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--sbom-dir", type=Path, required=True)
    p.add_argument("--provenance-url")
    p.add_argument("--sbom-url", action="append", default=[], metavar="PLATFORM=URL")
    p.set_defaults(func=cmd_release_notes)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
