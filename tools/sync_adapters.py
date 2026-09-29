#!/usr/bin/env python3
"""Synchronize self-contained Codex and Claude adapter skill copies."""

from __future__ import annotations

from pathlib import Path
import shutil
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts._update_common import UpdateError, ensure_safe_path


SKILL_NAME = "cloudrelay-mediagen"
MANAGED_FILES = (
    Path("SKILL.md"),
    Path("VERSION"),
    Path("agents/openai.yaml"),
    Path("scripts/configure_api_key.py"),
    Path("scripts/generate_image.py"),
    Path("scripts/generate_video.py"),
    Path("references/images.md"),
    Path("references/videos.md"),
    Path("scripts/_update_common.py"),
    Path("scripts/check_update.py"),
    Path("scripts/update.py"),
    Path("scripts/update.ps1"),
    Path("scripts/update.sh"),
)
ADAPTERS = (Path("adapters/codex"), Path("adapters/claude"))


def sync_adapter(adapter_root: Path) -> None:
    destination = REPOSITORY_ROOT / adapter_root / "skills" / SKILL_NAME
    try:
        ensure_safe_path(REPOSITORY_ROOT)
        ensure_safe_path(destination)
    except UpdateError as error:
        raise RuntimeError(str(error)) from error
    for relative in MANAGED_FILES:
        source = REPOSITORY_ROOT / relative
        if not source.is_file() or source.is_symlink():
            raise FileNotFoundError(source)
        target = destination / relative
        try:
            ensure_safe_path(source)
            ensure_safe_path(target)
        except UpdateError as error:
            raise RuntimeError(str(error)) from error
        if target.exists() and not target.is_file():
            raise RuntimeError(f"Refusing to replace a non-regular adapter file: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def main() -> int:
    for adapter in ADAPTERS:
        sync_adapter(adapter)
        print(f"Synchronized {adapter / 'skills' / SKILL_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
