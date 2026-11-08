#!/usr/bin/env python3
"""CI entry point: one argparse subcommand per workflow decision."""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from factory_ci import appversion, cleanup, description, images, notes, registry, versioning  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]

NONE_ENTRY = {
    "image": "__none__", "platform": "__none__", "slug": "none",
    "runner": "ubuntu-24.04", "qemu": False, "cst_arch": "amd64",
}
RELEASE_NONE_ENTRY = {**NONE_ENTRY, "version": "__none__"}


def release_plan(root: Path, head_sha: str,
                 revision_lookup: Callable[..., str | None]) -> tuple[list[dict], list[dict]]:
    """Plan releases for images with changes.

    A version already published from head_sha or one of its ancestors is resumed from that
    revision. Any other published revision is an error for that image only.
    revision_lookup(ref, never_released=...) returns the published revision or None.
    Returns (plans, errors).
    """
    plans, errors = [], []
    for image in images.list_images(root):
        plan = versioning.next_version(root, image, head_sha)
        if plan is None:
            continue
        published = revision_lookup(f"{registry.REGISTRY}/{image}:{plan.version}",
                                    never_released=plan.previous_version is None)
        if published is None:
            mode, sha = "build", head_sha
        elif published == head_sha or (
                published != registry.UNKNOWN_REVISION
                and versioning.is_ancestor(root, published, head_sha)):
            mode, sha = "resume", published
        else:
            errors.append({
                "image": image, "version": plan.version,
                "error": f"{image}:{plan.version} already published from {published}, "
                         f"which is not an ancestor of {head_sha}",
            })
            continue
        plans.append({
            "image": image, "version": plan.version,
            "previous_version": plan.previous_version, "bump": plan.bump,
            "mode": mode, "sha": sha,
        })
    return plans, errors


def cmd_pr_matrix(args) -> int:
    changed = images.changed_images(args.root, args.base, args.head)
    matrix = images.build_matrix(args.root, changed)
    print(f"matrix={json.dumps(matrix or [NONE_ENTRY], separators=(',', ':'))}")
    print(f"has_images={'true' if matrix else 'false'}")
    return 0


def cmd_release_plan(args) -> int:
    plans, errors = release_plan(args.root, args.head_sha, registry.index_revision)
    for error in errors:
        print(f"::error::{error['image']}: {error['error']}", file=sys.stderr)
    versions = {p["image"]: p["version"] for p in plans if p["mode"] == "build"}
    matrix = [{**entry, "version": versions[entry["image"]]}
              for entry in images.build_matrix(args.root, list(versions))]
    print(f"plans={json.dumps(plans, separators=(',', ':'))}")
    print(f"matrix={json.dumps(matrix or [RELEASE_NONE_ENTRY], separators=(',', ':'))}")
    print(f"has_builds={'true' if matrix else 'false'}")
    print(f"has_releases={'true' if plans else 'false'}")
    print(f"errors={json.dumps(errors, separators=(',', ':'))}")
    return 0


def cmd_description(args) -> int:
    print(description.package_description(args.root, args.image, args.sha))
    return 0


def cmd_readme_cell(args) -> int:
    dockerfile = images.read_dockerfile(args.root, args.image)
    print(appversion.readme_cell(appversion.app_versions(args.image, dockerfile)))
    return 0


def cmd_release_notes(args) -> int:
    repo = os.environ.get("GITHUB_REPOSITORY", "bill-of-materials/factory")
    server_url = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    ref = f"{registry.REGISTRY}/{args.image}"
    index_digest = args.digest or registry.digest_of(f"{ref}:{args.version}")
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
            print(f"::warning::{args.image} {args.version}: no SBOM attestation for {platform} "
                  f"({digest})", file=sys.stderr)
            continue
        name = f"{args.image}-{args.version}-{images.platform_slug(platform)}.spdx.json"
        (args.sbom_dir / name).write_text(json.dumps(sboms[0].predicate))

    ctx = notes.ReleaseContext(
        image=args.image, version=args.version, previous_version=previous,
        index_digest=index_digest, platforms=platforms, commits=commits, repo=repo,
        server_url=server_url, sha=args.sha, run_id=args.run_id,
        provenance_url=args.provenance_url, sbom_urls=sbom_urls,
        apps=appversion.app_versions(
            args.image, images.read_dockerfile(args.root, args.image, args.sha)),
    )
    args.out.write_text(notes.render_release_notes(ctx))
    return 0


def cleanup_image(org: str, image: str, repo: str, dry_run: bool) -> None:
    """Delete (or list, in dry-run) the unneeded versions of one image.

    Raises before any deletion when the version graph cannot be fully established.
    """
    ref = f"{registry.REGISTRY}/{image}"
    versions = registry.list_versions(org, image)
    if not versions:
        print(f"{image}: no package versions")
        return
    listed = {v.digest for v in versions}
    children: dict[str, list[str]] = {}
    subjects: dict[str, str] = {}
    pending = [v.digest for v in versions]
    while pending:
        digest = pending.pop()
        if digest in children:
            continue
        links = registry.manifest_links(f"{ref}@{digest}")
        children[digest] = links.children
        if links.subject:
            subjects[digest] = links.subject
        pending.extend(c for c in children[digest] if c not in listed and c not in children)

    now = datetime.now(timezone.utc)
    selection = cleanup.select(versions, children, now, subjects=subjects)
    print(f"{image}: kept {len(selection.keep & listed)}, deleting {len(selection.delete)}")
    for v in versions:
        if v.digest in selection.keep:
            print(f"keeping {v.digest} {','.join(v.tags)}".rstrip())
    prefix = "DRY-RUN would delete" if dry_run else "deleting"
    for v in selection.delete:
        print(f"{prefix} {v.id} {v.digest} {','.join(v.tags)}")
    if dry_run:
        return
    for v in selection.delete:
        registry.delete_version(org, image, v.id)
    for release in selection.removed_releases:
        registry.mark_release_removed(repo, image, release, now.date())


def cmd_cleanup(args) -> int:
    repo = os.environ.get("GITHUB_REPOSITORY", "bill-of-materials/factory")
    names = [args.image] if args.image else images.list_images(args.root)
    failed = []
    for image in names:
        try:
            cleanup_image(args.org, image, repo, args.dry_run)
        except Exception as exc:  # noqa: BLE001 - per-image isolation; nothing more is deleted for it
            print(f"ERROR {image}: cleanup aborted, no further deletions for this image: {exc!r}")
            failed.append(image)
    if failed:
        print(f"ERROR cleanup failed for: {', '.join(failed)}")
    return 1 if failed else 0


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
    p.add_argument("--sha", help="read the Dockerfile at this commit; default: working tree")
    p.set_defaults(func=cmd_description)

    p = sub.add_parser("readme-cell", help="print the README Application cell for an image")
    p.add_argument("--image", required=True)
    p.set_defaults(func=cmd_readme_cell)

    p = sub.add_parser("release-notes", help="render release notes and extract SBOM files")
    p.add_argument("--image", required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--sha", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--sbom-dir", type=Path, required=True)
    p.add_argument("--digest", help="index digest; default: resolve the VERSION tag")
    p.add_argument("--provenance-url")
    p.add_argument("--sbom-url", action="append", default=[], metavar="PLATFORM=URL")
    p.set_defaults(func=cmd_release_notes)

    p = sub.add_parser("cleanup", help="delete GHCR versions outside the retention set")
    p.add_argument("--org", required=True)
    p.add_argument("--image")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_cleanup)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
