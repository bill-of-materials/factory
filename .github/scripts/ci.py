#!/usr/bin/env python3
"""CI entry point: one argparse subcommand per workflow decision."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from factory_ci import description, images  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]

NONE_ENTRY = {
    "image": "__none__", "platform": "__none__", "slug": "none",
    "runner": "ubuntu-24.04", "qemu": False, "cst_arch": "amd64",
}


def cmd_pr_matrix(args) -> int:
    changed = images.changed_images(args.root, args.base, args.head)
    matrix = images.build_matrix(args.root, changed)
    print(f"matrix={json.dumps(matrix or [NONE_ENTRY], separators=(',', ':'))}")
    print(f"has_images={'true' if matrix else 'false'}")
    return 0


def cmd_description(args) -> int:
    print(description.package_description(args.root, args.image))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ci.py")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("pr-matrix", help="build matrix for images changed in a PR")
    p.add_argument("--base", required=True)
    p.add_argument("--head", required=True)
    p.set_defaults(func=cmd_pr_matrix)

    p = sub.add_parser("description", help="print the package description for an image")
    p.add_argument("--image", required=True)
    p.set_defaults(func=cmd_description)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
