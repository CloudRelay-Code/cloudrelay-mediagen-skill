#!/usr/bin/env python3
"""Build a Gemini CLI-compatible .skill archive from the runtime files."""

from __future__ import annotations

import argparse
import ast
import hashlib
import os
import stat
import sys
import tempfile
from pathlib import Path
import zipfile


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
from scripts._update_common import UpdateError, ensure_safe_path, validate_skill_tree

SKILL_NAME = "cloudrelay-mediagen"
ASSET_NAME = "cloudrelay-mediagen.skill"
REPOSITORY = "CloudRelay-Code/cloudrelay-mediagen-skill"
PACKAGE_FILES = (
    Path("SKILL.md"),
    Path("VERSION"),
    Path("agents/openai.yaml"),
    Path("scripts/configure_api_key.py"),
    Path("scripts/generate_image.py"),
    Path("scripts/generate_video.py"),
    Path("scripts/migrate_from_imagegen.py"),
    Path("references/migration.md"),
    Path("references/images.md"),
    Path("references/videos.md"),
    Path("scripts/_update_common.py"),
    Path("scripts/check_update.py"),
    Path("scripts/update.py"),
    Path("scripts/update.ps1"),
    Path("scripts/update.sh"),
)


def build_package(source_root: Path, output_dir: Path) -> Path:
    source_root = source_root.expanduser().absolute()
    output_dir = output_dir.expanduser().absolute()
    ensure_safe_path(source_root)
    missing = [path for path in PACKAGE_FILES if not (source_root / path).is_file()]
    if missing:
        raise FileNotFoundError("Missing package file(s): " + ", ".join(map(str, missing)))
    validate_skill_tree(source_root)
    # Read constants without importing or executing the source being packaged.
    tree = ast.parse((source_root / "scripts/_update_common.py").read_text(encoding="utf-8"))
    constants = {
        target.id: node.value.value
        for node in tree.body if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
        for target in node.targets if isinstance(target, ast.Name)
    }
    for name, expected in (("SKILL_NAME", SKILL_NAME), ("ASSET_NAME", ASSET_NAME), ("REPOSITORY", REPOSITORY)):
        if constants.get(name) != expected:
            raise UpdateError(f"Package source has the wrong {name} release channel.")
    for relative in PACKAGE_FILES:
        source = source_root / relative
        ensure_safe_path(source)
        if not stat.S_ISREG(source.lstat().st_mode):
            raise UpdateError(f"Package source is not a regular file: {relative}")
    ensure_safe_path(output_dir)
    output = output_dir / ASSET_NAME
    ensure_safe_path(output)
    if output.exists() and not stat.S_ISREG(output.lstat().st_mode):
        raise UpdateError("Package output must be a regular file.")
    if output in (source_root / path for path in PACKAGE_FILES):
        raise UpdateError("Package output overlaps its source.")
    output_dir.mkdir(parents=True, exist_ok=True)
    # Fixed timestamps/modes make the same bytes reproducible across hosts.
    fd, temporary_name = tempfile.mkstemp(prefix=".cloudrelay-package-", suffix=".tmp", dir=output_dir)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for relative in PACKAGE_FILES:
                source = source_root / relative
                ensure_safe_path(source)
                info = zipfile.ZipInfo(relative.as_posix(), date_time=(2020, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, source.read_bytes())
        ensure_safe_path(output)
        temporary.replace(output)
    finally:
        if temporary.exists():
            temporary.unlink()
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Package CloudRelay MediaGen.")
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    args = parser.parse_args()
    source_root = Path(__file__).resolve().parent.parent
    output = build_package(source_root, args.output_dir)
    print(f"Packaged {output}")
    print(f"SHA-256: {hashlib.sha256(output.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
