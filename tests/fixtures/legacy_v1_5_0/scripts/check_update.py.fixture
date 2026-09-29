#!/usr/bin/env python3
"""Check the latest CloudRelay ImageGen release without changing files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

try:
    from _update_common import (
        ASSET_NAME,
        UpdateError,
        compare_versions,
        fetch_latest_release,
        local_version,
    )
except ImportError:
    from ._update_common import (
        ASSET_NAME,
        UpdateError,
        compare_versions,
        fetch_latest_release,
        local_version,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check for a CloudRelay ImageGen update.")
    parser.add_argument(
        "--target-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Installed skill directory (defaults to this skill).",
    )
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Keep lookup failures non-fatal for use during normal skill activation.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    target = args.target_dir.expanduser().resolve()
    try:
        current = local_version(target)
        latest = fetch_latest_release(timeout=args.timeout)
        if current is None:
            status = "unknown-current"
        else:
            comparison = compare_versions(latest.version, current)
            status = (
                "update-available"
                if comparison > 0
                else "local-newer"
                if comparison < 0
                else "up-to-date"
            )
        result = {
            "repository": "CloudRelay-Code/cloudrelay-imagegen-skill",
            "current": current.normalized if current else None,
            "latest": latest.version.normalized,
            "tag": latest.tag_name,
            "status": status,
            "asset": ASSET_NAME if latest.asset_url else None,
            "digest_verified_on_update": bool(latest.asset_digest),
            "published_at": latest.published_at,
        }
        if args.as_json:
            print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        elif not args.quiet or status != "up-to-date":
            print(
                f"CloudRelay ImageGen: current={result['current'] or 'unknown'} "
                f"latest={result['latest']} status={status}"
            )
        return 0
    except (UpdateError, ValueError) as error:
        if args.quiet:
            return 0
        print(f"Update check failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
