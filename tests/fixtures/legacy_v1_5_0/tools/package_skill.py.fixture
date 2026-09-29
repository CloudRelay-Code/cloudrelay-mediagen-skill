#!/usr/bin/env python3
"""Build a Gemini CLI-compatible .skill archive from the runtime files."""

from __future__ import annotations

import argparse
from pathlib import Path
import zipfile


SKILL_NAME = "cloudrelay-imagegen"
PACKAGE_FILES = (
    Path("SKILL.md"),
    Path("VERSION"),
    Path("agents/openai.yaml"),
    Path("scripts/configure_api_key.py"),
    Path("scripts/generate_image.py"),
    Path("scripts/_update_common.py"),
    Path("scripts/check_update.py"),
    Path("scripts/update.py"),
    Path("scripts/update.ps1"),
    Path("scripts/update.sh"),
)


def build_package(source_root: Path, output_dir: Path) -> Path:
    missing = [path for path in PACKAGE_FILES if not (source_root / path).is_file()]
    if missing:
        names = ", ".join(str(path) for path in missing)
        raise FileNotFoundError(f"Missing package file(s): {names}")

    output_dir.mkdir(parents=True, exist_ok=True)
    output = (output_dir / f"{SKILL_NAME}.skill").resolve()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative_path in PACKAGE_FILES:
            archive.write(source_root / relative_path, relative_path.as_posix())
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Package CloudRelay ImageGen.")
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    args = parser.parse_args()
    source_root = Path(__file__).resolve().parent.parent
    output = build_package(source_root, args.output_dir)
    print(f"Packaged {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
