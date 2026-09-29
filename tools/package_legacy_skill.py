#!/usr/bin/env python3
"""Build only the independent CloudRelay ImageGen 1.5.x runtime archive.

No code from a caller-supplied source tree is imported or executed. This builder
never delegates to the MediaGen builder and cannot change release identity.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile
import zipfile

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_ROOT = REPOSITORY_ROOT / "legacy" / "cloudrelay-imagegen"
SKILL_NAME = "cloudrelay-imagegen"
ASSET_NAME = "cloudrelay-imagegen.skill"
REPOSITORY = "CloudRelay-Code/cloudrelay-imagegen-skill"
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
MAX_PACKAGE_BYTES = 64 * 1024 * 1024


class PackageError(RuntimeError):
    """The supplied source or output is unsafe or belongs to another channel."""


def _safe_path(path: Path) -> Path:
    # Do not resolve first: that would hide symlink or junction components.
    path = path.expanduser()
    if ".." in path.parts:
        raise PackageError(f"Parent traversal is not permitted: {path}")
    path = path.absolute()
    for component in (path, *path.parents):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise PackageError(f"Cannot inspect package path: {component}") from error
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise PackageError(f"Package paths cannot contain symlinks or junctions: {component}")
        if component != path and not stat.S_ISDIR(info.st_mode):
            raise PackageError(f"Package path parent is not a directory: {component}")
    return path


def _literal_assignments(source: str) -> dict[str, ast.AST]:
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        raise PackageError("Legacy updater is not valid Python source.") from error
    wanted = {"SKILL_NAME", "ASSET_NAME", "REPOSITORY", "MANAGED_FILES", "REQUIRED_FILES"}
    declarations: dict[str, ast.AST] = {}
    # Each release binding must be declared exactly once at module scope.
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in wanted:
                    if target.id in declarations or len(node.targets) != 1:
                        raise PackageError(f"Ambiguous legacy updater binding: {target.id}")
                    declarations[target.id] = node.value
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id in wanted:
            count = sum(1 for item in ast.walk(tree) if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store) and item.id == node.id)
            if count != 1:
                raise PackageError(f"Legacy updater reassigns release binding: {node.id}")
    if declarations.keys() != wanted:
        raise PackageError("Legacy updater must declare all fixed channel and manifest bindings.")
    return declarations


def _path_tuple(node: ast.AST) -> tuple[Path, ...]:
    if not isinstance(node, ast.Tuple):
        raise PackageError("Legacy updater manifests must be literal Path tuples.")
    result = []
    for item in node.elts:
        if not (isinstance(item, ast.Call) and isinstance(item.func, ast.Name) and item.func.id == "Path" and len(item.args) == 1 and not item.keywords and isinstance(item.args[0], ast.Constant) and isinstance(item.args[0].value, str)):
            raise PackageError("Legacy updater manifests must contain literal Path strings.")
        result.append(Path(item.args[0].value))
    return tuple(result)


def _validate_payload(files: dict[Path, bytes]) -> None:
    try:
        skill = files[Path("SKILL.md")].decode("utf-8")
        version = files[Path("VERSION")].decode("ascii").strip()
        updater = files[Path("scripts/_update_common.py")].decode("utf-8")
    except UnicodeError as error:
        raise PackageError("Legacy identity, version and updater files must be text.") from error
    frontmatter = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", skill, re.DOTALL)
    names = re.findall(r"^name:[ \t]*([^\r\n]+)$", frontmatter.group(1), re.MULTILINE) if frontmatter else []
    allowed_names = (SKILL_NAME, chr(34) + SKILL_NAME + chr(34), chr(39) + SKILL_NAME + chr(39))
    if len(names) != 1 or names[0].strip() not in allowed_names:
        raise PackageError(f"Legacy package requires name: {SKILL_NAME} frontmatter.")
    header = frontmatter.group(1)
    for line in header.splitlines():
        if not line.strip() or line.startswith("#") or line[0].isspace():
            continue
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_-]*:[ 	]*.*$", line):
            raise PackageError("Legacy frontmatter requires canonical unquoted mapping keys.")
    all_names = re.findall(r"""(?m)^[ 	]*(?:name|["']name["'])[ 	]*:[ 	]*(.*?)[ 	]*$""", header)
    if len(all_names) != 1:
        raise PackageError("Legacy frontmatter must contain exactly one name field.")
    for relative, data in files.items():
        if relative.suffix == ".py":
            try:
                compile(data.decode("utf-8"), str(relative), "exec")
            except (SyntaxError, UnicodeError) as error:
                raise PackageError(f"Legacy Python validation failed for {relative}.") from error
    for content in files.values():
        if re.search(rb"(?m)^[ \t]*name:[ \t]*[\"']?cloudrelay-mediagen(?:[\"']?[ \t\r]*$)", content):
            raise PackageError("MediaGen frontmatter cannot appear in the legacy archive.")
    if not re.fullmatch(r"1\.5\.(?:0|[1-9][0-9]*)", version):
        raise PackageError("Legacy package VERSION must be a stable 1.5.x version.")
    declarations = _literal_assignments(updater)
    for name, expected in (("SKILL_NAME", SKILL_NAME), ("ASSET_NAME", ASSET_NAME), ("REPOSITORY", REPOSITORY)):
        node = declarations[name]
        if not isinstance(node, ast.Constant) or node.value != expected:
            raise PackageError(f"Legacy package has the wrong literal {name} release channel.")
    for name in ("MANAGED_FILES", "REQUIRED_FILES"):
        if _path_tuple(declarations[name]) != PACKAGE_FILES:
            raise PackageError(f"Legacy {name} must equal the original ten runtime files.")


def build_package(source_root: Path, output_dir: Path) -> Path:
    """Validate an explicit independent source tree and atomically build its ZIP."""
    source_root = _safe_path(Path(source_root))
    output_dir = _safe_path(Path(output_dir))
    if not source_root.is_dir():
        raise PackageError(f"Legacy source directory does not exist: {source_root}")
    if output_dir.exists() and not output_dir.is_dir():
        raise PackageError("Package output directory is not a directory.")
    files: dict[Path, bytes] = {}
    size = 0
    for relative in PACKAGE_FILES:
        source = _safe_path(source_root / relative)
        try:
            info = source.lstat()
        except FileNotFoundError as error:
            raise PackageError(f"Missing legacy package file: {relative}") from error
        if not stat.S_ISREG(info.st_mode):
            raise PackageError(f"Legacy source must be a regular file: {relative}")
        size += info.st_size
        if size > MAX_PACKAGE_BYTES:
            raise PackageError("Legacy package exceeds its size limit.")
        files[relative] = source.read_bytes()
    # Validate and package the same byte snapshot; the source is never executed.
    _validate_payload(files)
    output = _safe_path(output_dir / ASSET_NAME)
    if output.exists() and not stat.S_ISREG(output.lstat().st_mode):
        raise PackageError("Legacy package output must be a regular file.")
    if output in (source_root / path for path in PACKAGE_FILES):
        raise PackageError("Package output overlaps its source.")
    output_dir.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".cloudrelay-legacy-package-", suffix=".tmp", dir=output_dir)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for relative in PACKAGE_FILES:
                info = zipfile.ZipInfo(relative.as_posix(), date_time=(2020, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, files[relative])
        _safe_path(output)
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Package the independent CloudRelay ImageGen 1.5.x source.")
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--output-dir", type=Path, default=REPOSITORY_ROOT / "dist")
    args = parser.parse_args(argv)
    try:
        output = build_package(args.source_root, args.output_dir)
    except (PackageError, OSError) as error:
        parser.exit(1, f"Legacy packaging failed: {error}\n")
    print(f"Packaged {output}")
    print(f"SHA-256: {hashlib.sha256(output.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
