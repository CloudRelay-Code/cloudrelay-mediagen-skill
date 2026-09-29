#!/usr/bin/env python3
"""Install CloudRelay MediaGen into supported agent skill directories."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import shutil
import stat
import tempfile
import sys

from scripts._update_common import UpdateError, ensure_safe_path, validate_skill_identity


SKILL_NAME = "cloudrelay-mediagen"
CORE_FILES = (
    Path("SKILL.md"),
    Path("VERSION"),
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
CODEX_FILES = (Path("agents/openai.yaml"),)


@dataclass(frozen=True)
class ClientSpec:
    user_root: Path
    project_root: Path
    reload_hint: str


CLIENTS = {
    "codex": ClientSpec(
        Path(".codex/skills"),
        Path(".agents/skills"),
        "Skills reload automatically; restart Codex if the skill is missing.",
    ),
    "claude-code": ClientSpec(
        Path(".claude/skills"),
        Path(".claude/skills"),
        "Start a new Claude Code session.",
    ),
    "gemini-cli": ClientSpec(
        Path(".gemini/skills"),
        Path(".gemini/skills"),
        "Run /skills reload in Gemini CLI, then /skills list.",
    ),
    "openclaw": ClientSpec(
        Path(".openclaw/skills"),
        Path("skills"),
        "Wait for the skill watcher and the next Agent turn; restart the Gateway if watching is disabled.",
    ),
    "cursor": ClientSpec(
        Path(".cursor/skills"),
        Path(".cursor/skills"),
        "Restart Cursor, then confirm the skill is listed.",
    ),
}


class InstallError(RuntimeError):
    """Raised for invalid or unsafe installation requests."""


def destination_for(
    client: str,
    scope: str,
    *,
    home: Path,
    project_dir: Path,
) -> Path:
    spec = CLIENTS[client]
    root = home / spec.user_root if scope == "user" else project_dir / spec.project_root
    return root.expanduser().absolute() / SKILL_NAME


def _validate_source(source_root: Path, client: str) -> tuple[Path, ...]:
    files = CORE_FILES + (CODEX_FILES if client == "codex" else ())
    missing = [str(path) for path in files if not (source_root / path).is_file()]
    if missing:
        raise InstallError("Missing required source file(s): " + ", ".join(missing))
    try:
        ensure_safe_path(source_root)
        validate_skill_identity(source_root, SKILL_NAME)
        for relative in files:
            ensure_safe_path(source_root / relative)
            if not stat.S_ISREG((source_root / relative).lstat().st_mode):
                raise InstallError(f"Source is not a regular file: {relative}")
    except UpdateError as error:
        raise InstallError(str(error)) from error
    return files


def _validate_destination(destination: Path, files: tuple[Path, ...]) -> None:
    try:
        ensure_safe_path(destination)
        if destination.exists():
            if not destination.is_dir():
                raise InstallError("Destination must be a skill directory.")
            if (destination / "SKILL.md").exists():
                validate_skill_identity(destination, SKILL_NAME)
            elif any(destination.iterdir()):
                raise InstallError("Refusing a nonempty destination without a media skill identity.")
        for relative in files:
            target = destination / relative
            ensure_safe_path(target)
            for parent in target.parents:
                if parent.exists() and not parent.is_dir():
                    raise InstallError(f"Destination parent is not a directory: {parent}")
            if target.exists() and not stat.S_ISREG(target.lstat().st_mode):
                raise InstallError(f"Refusing to replace a non-regular file: {target}")
    except UpdateError as error:
        raise InstallError(str(error)) from error


def install_client(
    client: str,
    scope: str,
    *,
    source_root: Path,
    home: Path,
    project_dir: Path,
    force: bool = False,
    dry_run: bool = False,
) -> Path:
    files = _validate_source(source_root, client)
    destination = destination_for(
        client,
        scope,
        home=home,
        project_dir=project_dir,
    )
    _validate_destination(destination, files)
    if destination.exists() and not force and not dry_run:
        raise InstallError(
            f"Destination already exists: {destination}. Re-run with --force to update known files."
        )
    if dry_run:
        return destination

    try:
        ensure_safe_path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        for relative_path in files:
            target = destination / relative_path
            ensure_safe_path(target)
            if target.exists() and not target.is_file():
                raise InstallError(f"Refusing to replace a non-regular file: {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            # Replace the directory entry, never truncate a possible hard link
            # to an unrelated user file (same convention as the updater).
            with tempfile.NamedTemporaryFile(prefix=".cloudrelay-install-", dir=target.parent, delete=False) as stream:
                temporary = Path(stream.name)
            try:
                shutil.copy2(source_root / relative_path, temporary)
                ensure_safe_path(target)
                temporary.replace(target)
            finally:
                if temporary.exists():
                    temporary.unlink()
    except UpdateError as error:
        raise InstallError(str(error)) from error
    return destination


def _selected_clients(values: list[str]) -> list[str]:
    if "all" in values:
        return list(CLIENTS)
    return list(dict.fromkeys(values))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install CloudRelay MediaGen for one or more agent clients."
    )
    parser.add_argument(
        "--client",
        action="append",
        required=True,
        choices=("all", *CLIENTS),
        help="Target client. Repeat the option or use 'all'.",
    )
    parser.add_argument(
        "--scope",
        choices=("user", "project"),
        default="user",
        help="Install for the current user or one project.",
    )
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=Path.cwd(),
        help="Project root used with --scope project. Defaults to the current directory.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Update known skill files when the destination already exists.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print destinations without writing files.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source_root = Path(__file__).resolve().parent
    clients = _selected_clients(args.client)
    home = Path.home()

    try:
        destinations = [
            (
                client,
                destination_for(
                    client,
                    args.scope,
                    home=home,
                    project_dir=args.project_dir,
                ),
            )
            for client in clients
        ]
        for client, destination in destinations:
            files = _validate_source(source_root, client)
            _validate_destination(destination, files)
            if destination.exists() and not args.force and not args.dry_run:
                raise InstallError(
                    f"Destination already exists: {destination}. "
                    "Re-run with --force to update known files."
                )
        for client, _ in destinations:
            install_client(
                client,
                args.scope,
                source_root=source_root,
                home=home,
                project_dir=args.project_dir,
                force=args.force,
                dry_run=args.dry_run,
            )
    except (InstallError, OSError) as error:
        print(f"Installation failed: {error}", file=sys.stderr)
        return 1

    verb = "Would install" if args.dry_run else "Installed"
    for client, destination in destinations:
        print(f"{verb} {client}: {destination}")
        if not args.dry_run:
            print(f"  {CLIENTS[client].reload_hint}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
