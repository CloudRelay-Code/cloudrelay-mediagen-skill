#!/usr/bin/env python3
"""Explicit metadata handoff from ImageGen; never install or copy skill content.

Credential reporting uses names and filesystem metadata only. A present setting
is not a validation of its value. Persisted Windows environment values are not
queried; start a fresh terminal to include their names in the process environment.
"""

from __future__ import annotations

import sys

# Keep default and dry-run execution read-only, including helper imports.
sys.dont_write_bytecode = True

import argparse
from dataclasses import asdict, dataclass, replace
import json
import os
from pathlib import Path, PureWindowsPath
import re
import stat

if __package__:
    from ._update_common import UpdateError, ensure_safe_path, validate_skill_identity
    from .configure_api_key import _SECRET_FILE_NAMES, PROVIDER_ENV_NAMES
else:  # Direct execution from an installed skill's scripts directory.
    from _update_common import UpdateError, ensure_safe_path, validate_skill_identity
    from configure_api_key import _SECRET_FILE_NAMES, PROVIDER_ENV_NAMES


LEGACY_SKILL_NAME = "cloudrelay-imagegen"
SKILL_NAME = "cloudrelay-mediagen"
RECEIPT_NAME = ".cloudrelay-imagegen-migration.json"
OUTPUT_DIRECTORY_NAMES = ("generated-images", "generated-videos")
INSTALL_COMMAND = (
    'npx --yes skills add "https://github.com/CloudRelay-Code/'
    'cloudrelay-mediagen-skill" --skill "cloudrelay-mediagen" --global --yes'
)
RELOAD_HINTS = {
    "Codex": "Skills reload automatically; restart Codex if the skill is missing.",
    "Claude Code": "Start a new Claude Code session.",
    "Gemini CLI": "Run /skills reload, then /skills list.",
    "OpenClaw": "Wait for the skill watcher and the next Agent turn; restart the Gateway if watching is disabled.",
    "Cursor": "Restart Cursor, then confirm the skill is listed.",
}
_RESERVED_COMPONENT = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.I)


class MigrationError(RuntimeError):
    """Raised when a metadata handoff cannot be performed safely."""


@dataclass(frozen=True)
class MigrationReport:
    source_dir: Path
    target_dir: Path
    legacy_detected: bool
    target_installed: bool
    credential_presence: dict[str, bool]
    output_directories: dict[str, bool]
    action: str = "dry-run"


def _checked_path(value: str | Path, label: str) -> Path:
    raw = os.fspath(value)
    if not raw or any(ord(character) < 32 for character in raw):
        raise MigrationError(f"{label} path is empty or contains control characters.")
    normalized = raw.replace("\\", "/")
    if normalized.startswith("//"):
        raise MigrationError(f"{label} must be a local path, not a UNC/device path.")
    if any(part in {".", ".."} for part in normalized.split("/")):
        raise MigrationError(f"{label} path contains traversal or dot components.")
    windows_path = PureWindowsPath(raw)
    if os.name == "nt" and windows_path.drive and not windows_path.root:
        raise MigrationError(f"{label} must not be a drive-relative path.")
    if os.name != "nt" and (windows_path.drive or "\\" in raw):
        raise MigrationError(f"{label} uses Windows syntax on a non-Windows host.")
    path = Path(raw).absolute()
    if path.parent == path:
        raise MigrationError(f"{label} must not be a filesystem root.")
    for component in path.parts[1:]:
        if (
            component.endswith((".", " "))
            or any(character in component for character in ':<>"|?*')
            or _RESERVED_COMPONENT.match(component)
        ):
            raise MigrationError(f"{label} contains an unsafe path component.")
    ensure_safe_path(path)
    return path


def _validate_identity(directory: Path, name: str) -> None:
    ensure_safe_path(directory / "SKILL.md")
    metadata = (directory / "SKILL.md").lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise MigrationError(f"SKILL.md is not a regular file: {directory}")
    validate_skill_identity(directory, expected_name=name)


def _validated_directories(
    source_dir: str | Path, target_dir: str | Path
) -> tuple[Path, Path, bool]:
    source = _checked_path(source_dir, "Source")
    target = _checked_path(target_dir, "Target")
    if source == target or source in target.parents or target in source.parents:
        raise MigrationError("Source and target must be separate, non-nested directories.")
    if not source.is_dir():
        raise MigrationError(f"Legacy source directory does not exist: {source}")
    _validate_identity(source, LEGACY_SKILL_NAME)
    installed = target.exists()
    if installed:
        if not target.is_dir():
            raise MigrationError(f"Target is not a directory: {target}")
        _validate_identity(target, SKILL_NAME)
    elif target.name != SKILL_NAME:
        raise MigrationError(
            f"An uninstalled target must have the exact directory name {SKILL_NAME}."
        )
    return source, target, installed


def _default_config_dir() -> Path:
    # This is a location setting, never an API-key environment value.
    config_home = os.environ.get("XDG_CONFIG_HOME")
    root = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return root / "cloudrelay"


def _regular_file_present(path: Path) -> bool:
    ensure_safe_path(path)
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except FileNotFoundError:
        return False


def _directory_present(path: Path) -> bool:
    ensure_safe_path(path)
    try:
        return stat.S_ISDIR(path.lstat().st_mode)
    except FileNotFoundError:
        return False


def inspect_migration(
    source_dir: str | Path,
    target_dir: str | Path,
    *,
    config_dir: Path | None = None,
) -> MigrationReport:
    """Read only identities and presence metadata; never read credential values."""
    try:
        source, target, installed = _validated_directories(source_dir, target_dir)
        shared_config = _default_config_dir() if config_dir is None else Path(config_dir)
        ensure_safe_path(shared_config)
        # Iterating names avoids Mapping.__contains__/get, which can fetch values.
        environment_names = frozenset(iter(os.environ))
        credentials = {}
        for provider, filename in _SECRET_FILE_NAMES.items():
            locations = (source / filename, target / filename, shared_config / filename)
            file_presence = [_regular_file_present(location) for location in locations]
            credentials[provider] = (
                PROVIDER_ENV_NAMES[provider] in environment_names or any(file_presence)
            )
        outputs = {
            str(directory / name): _directory_present(directory / name)
            for directory in (source, target)
            for name in OUTPUT_DIRECTORY_NAMES
        }
        return MigrationReport(source, target, True, installed, credentials, outputs)
    except (UpdateError, OSError, ValueError) as error:
        raise MigrationError(str(error)) from error


def migrate(
    source_dir: str | Path,
    target_dir: str | Path,
    *,
    yes: bool = False,
    dry_run: bool = False,
    config_dir: Path | None = None,
) -> MigrationReport:
    """Optionally create one receipt. Runtime, keys and outputs are never copied."""
    report = inspect_migration(source_dir, target_dir, config_dir=config_dir)
    if dry_run or not yes:
        return report
    if not report.target_installed:
        return replace(report, action="installation-required")

    receipt = report.target_dir / RECEIPT_NAME
    try:
        # Recheck both identities and every path component immediately before writing.
        _validated_directories(report.source_dir, report.target_dir)
        ensure_safe_path(receipt)
        payload = asdict(report)
        payload.update(
            schema_version=1,
            source_dir=str(report.source_dir),
            target_dir=str(report.target_dir),
            source_identity=LEGACY_SKILL_NAME,
            target_identity=SKILL_NAME,
            action="metadata-handoff-recorded",
            credential_check="presence-only; no values read or validated",
            scope="Metadata receipt only; no runtime, credentials or user files copied.",
        )
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(receipt, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except FileExistsError as error:
        raise MigrationError(
            f"Existing receipt or user file was left unchanged: {receipt}"
        ) from error
    except (UpdateError, OSError, ValueError) as error:
        raise MigrationError(str(error)) from error
    return replace(report, action="metadata-handoff-recorded")


def format_report(report: MigrationReport) -> str:
    lines = [
        "Migration is a metadata handoff only; it does not install or copy skill content.",
        f"Old skill: {report.source_dir}",
        f"New skill: {report.target_dir}",
        f"Legacy skill detected: {'yes' if report.legacy_detected else 'no'}",
        f"Media skill installed: {'yes' if report.target_installed else 'no'}",
        f"Action: {report.action}",
        "API-key configuration (presence only; values are never read or validated):",
    ]
    for provider, present in report.credential_presence.items():
        lines.append(f"  {provider}: {'configured' if present else 'unconfigured'}")
    lines.extend([
        "Presence checks cover process variable names and standard key filenames only.",
        "Registry-only settings are not inspected; rerun from a fresh terminal if needed.",
        "Generated directories (left in their original locations):",
    ])
    lines.extend(
        f"  {path}: {'detected' if present else 'not detected'}"
        for path, present in report.output_directories.items()
    )
    if report.action == "metadata-handoff-recorded":
        lines.append(f"Created receipt: {report.target_dir / RECEIPT_NAME}")
    else:
        lines.append("No files were written. --yes records metadata only after separate installation.")
    if not report.target_installed:
        lines.extend([
            "Install the new skill separately, then rerun with its actual installed directory:",
            f"  {INSTALL_COMMAND}",
        ])
    lines.extend([
        "Legacy and media skills may coexist. Source, keys, outputs and user files stay unchanged.",
        "The legacy updater cannot upgrade to the media identity through a redirected URL.",
        "Reload or restart the Agent after installing the media skill:",
    ])
    lines.extend(f"  {agent}: {hint}" for agent, hint in RELOAD_HINTS.items())
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect legacy migration; explicitly record metadata only with --yes."
    )
    parser.add_argument("--source-dir", required=True, help="Existing cloudrelay-imagegen directory.")
    parser.add_argument("--target-dir", required=True, help="Separate cloudrelay-mediagen directory.")
    parser.add_argument("--dry-run", action="store_true", help="Never write; overrides --yes.")
    parser.add_argument("--yes", action="store_true", help="Create only an exclusive migration receipt.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = migrate(
            args.source_dir, args.target_dir, yes=args.yes, dry_run=args.dry_run
        )
    except MigrationError as error:
        print(f"Migration refused: {error}", file=sys.stderr)
        return 1
    print(format_report(report))
    return 1 if report.action == "installation-required" else 0


if __name__ == "__main__":
    raise SystemExit(main())
