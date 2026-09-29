#!/usr/bin/env python3
"""Safely check and explicitly apply a CloudRelay ImageGen release update."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

try:
    from _update_common import (
        UpdateError,
        apply_staged_release,
        compare_versions,
        download_release_asset,
        fetch_latest_release,
        local_version,
        stage_release,
        ensure_safe_path,
    )
except ImportError:
    from ._update_common import (
        UpdateError,
        apply_staged_release,
        compare_versions,
        download_release_asset,
        fetch_latest_release,
        local_version,
        stage_release,
        ensure_safe_path,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check and safely update an installed CloudRelay ImageGen skill."
    )
    parser.add_argument(
        "--target-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Installed skill directory (defaults to this skill).",
    )
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the verified release. Without this flag the command is read-only.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive confirmation (for trusted automation only).",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Apply an available verified update without prompting.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reinstall the same release when --apply is also provided.",
    )
    return parser


def _confirm(current: str | None, latest: str) -> bool:
    answer = input(
        f"Update CloudRelay ImageGen from {current or 'unknown'} to {latest}? [y/N] "
    )
    return answer.strip().lower() in {"y", "yes"}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Keep symlink/junction components visible to the safety check. Resolving
    # first would silently turn a linked target into an unrelated real path.
    target = args.target_dir.expanduser().absolute()
    if args.yes and not (args.apply or args.auto):
        print("Update failed: --yes requires --apply or --auto.", file=sys.stderr)
        return 1
    if args.auto:
        args.apply = True
        args.yes = True

    try:
        ensure_safe_path(target)
        if not target.is_dir() or not (target / "SKILL.md").is_file():
            print(
                f"Update failed: target is not an installed skill directory: {target}",
                file=sys.stderr,
            )
            return 1
        current = local_version(target)
        latest = fetch_latest_release(timeout=args.timeout)
        current_text = current.normalized if current else "unknown"
        print(f"CloudRelay ImageGen: current={current_text} latest={latest.version.normalized}")

        if current is not None:
            comparison = compare_versions(latest.version, current)
            if comparison < 0:
                print("Remote release is older than the installed version; refusing downgrade.")
                return 0
            if comparison == 0 and not args.force:
                print("Already up to date; no files changed.")
                return 0

        if not args.apply:
            print("No files changed. Re-run with --apply to install the verified release.")
            return 0
        if not args.yes and not _confirm(current_text, latest.version.normalized):
            print("Update canceled; no files changed.")
            return 0

        archive = download_release_asset(latest, timeout=args.timeout)
        stage, staged_version = stage_release(archive, target.parent)
        if compare_versions(staged_version, latest.version) != 0:
            raise UpdateError(
                f"Release tag {latest.tag_name} does not match archive VERSION {staged_version.normalized}."
            )
        apply_staged_release(stage, target)
        print(f"Updated CloudRelay ImageGen to {staged_version.normalized}: {target}")
        print("Restart the host agent or start a new task to load the updated skill.")
        return 0
    except (UpdateError, OSError, ValueError, EOFError) as error:
        print(f"Update failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
