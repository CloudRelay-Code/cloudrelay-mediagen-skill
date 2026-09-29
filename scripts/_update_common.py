#!/usr/bin/env python3
"""Shared, network-safe helpers for CloudRelay MediaGen updates."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tarfile
import tempfile
import time
from urllib.parse import quote, urlparse
import urllib.error
import urllib.request
import zipfile


SKILL_NAME = "cloudrelay-mediagen"
REPOSITORY = "CloudRelay-Code/cloudrelay-mediagen-skill"
LATEST_RELEASE_URL = (
    "https://api.github.com/repos/"
    f"{REPOSITORY}/releases/latest"
)
ASSET_NAME = "cloudrelay-mediagen.skill"
try:
    _installed_version = (Path(__file__).resolve().parents[1] / "VERSION").read_text(
        encoding="utf-8"
    ).strip()
except OSError:
    _installed_version = "unknown"
USER_AGENT = f"cloudrelay-mediagen-skill-updater/{_installed_version}"
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
MAX_EXTRACTED_BYTES = 64 * 1024 * 1024
LOCK_STALE_SECONDS = 6 * 60 * 60

# These are the files managed by the updater. User-created files and API-key
# storage are intentionally outside this list and are never touched.
MANAGED_FILES = (
    Path("SKILL.md"),
    Path("VERSION"),
    Path("agents/openai.yaml"),
    Path("scripts/configure_api_key.py"),
    Path("scripts/generate_image.py"),
    Path("scripts/generate_video.py"),
    Path("scripts/migrate_from_imagegen.py"),
    Path("references/images.md"),
    Path("references/videos.md"),
    Path("references/migration.md"),
    Path("scripts/_update_common.py"),
    Path("scripts/check_update.py"),
    Path("scripts/update.py"),
    Path("scripts/update.ps1"),
    Path("scripts/update.sh"),
)
REQUIRED_FILES = (
    Path("SKILL.md"),
    Path("VERSION"),
    Path("agents/openai.yaml"),
    Path("scripts/configure_api_key.py"),
    Path("scripts/generate_image.py"),
    Path("scripts/generate_video.py"),
    Path("scripts/migrate_from_imagegen.py"),
    Path("references/images.md"),
    Path("references/videos.md"),
    Path("references/migration.md"),
    Path("scripts/_update_common.py"),
    Path("scripts/check_update.py"),
    Path("scripts/update.py"),
    Path("scripts/update.ps1"),
    Path("scripts/update.sh"),
)
_VERSION_RE = re.compile(
    r"^[vV]?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-((?:0|[1-9][0-9]*|[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", re.DOTALL)


class UpdateError(RuntimeError):
    """Raised when a release cannot be safely checked or installed."""


@dataclass(frozen=True)
class SemanticVersion:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()
    build: tuple[str, ...] = ()

    @property
    def normalized(self) -> str:
        suffix = "-" + ".".join(self.prerelease) if self.prerelease else ""
        build = "+" + ".".join(self.build) if self.build else ""
        return f"{self.major}.{self.minor}.{self.patch}{suffix}{build}"


@dataclass(frozen=True)
class ReleaseInfo:
    version: SemanticVersion
    tag_name: str
    asset_url: str | None
    asset_digest: str | None
    published_at: str | None


def parse_version(value: str) -> SemanticVersion:
    text = value.strip()
    match = _VERSION_RE.fullmatch(text)
    if not match:
        raise UpdateError(f"Invalid semantic version: {value!r}")
    prerelease = tuple(match.group(4).split(".")) if match.group(4) else ()
    return SemanticVersion(
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
        prerelease,
        tuple(match.group(5).split(".")) if match.group(5) else (),
    )


def _compare_prerelease(left: tuple[str, ...], right: tuple[str, ...]) -> int:
    if not left and not right:
        return 0
    if not left:
        return 1
    if not right:
        return -1
    for left_part, right_part in zip(left, right):
        if left_part == right_part:
            continue
        left_numeric = left_part.isdigit()
        right_numeric = right_part.isdigit()
        if left_numeric and right_numeric:
            left_number = int(left_part)
            right_number = int(right_part)
            if left_number == right_number:
                continue
            return 1 if left_number > right_number else -1
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return 1 if left_part > right_part else -1
    if len(left) == len(right):
        return 0
    return 1 if len(left) > len(right) else -1


def compare_versions(left: SemanticVersion, right: SemanticVersion) -> int:
    left_core = (left.major, left.minor, left.patch)
    right_core = (right.major, right.minor, right.patch)
    if left_core != right_core:
        return 1 if left_core > right_core else -1
    return _compare_prerelease(left.prerelease, right.prerelease)


def local_version(skill_dir: Path) -> SemanticVersion | None:
    version_file = skill_dir / "VERSION"
    ensure_safe_path(version_file)
    if version_file.exists() and not _is_regular_file(version_file):
        raise UpdateError("VERSION must be a regular file.")
    try:
        return parse_version(version_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except OSError as error:
        raise UpdateError(f"Could not read {version_file}: {error}") from error


_ASSET_HOSTS = frozenset({
    "release-assets.githubusercontent.com",
    "objects.githubusercontent.com",
    "github-releases.githubusercontent.com",
})


def _validate_redirect_url(url: str, original_url: str) -> None:
    parsed = urlparse(url)
    if url == original_url:
        return
    if (original_url != LATEST_RELEASE_URL and parsed.scheme == "https"
            and parsed.netloc in _ASSET_HOSTS and not parsed.fragment):
        return
    raise UpdateError("Release redirect left the trusted HTTPS channel.")


class _ReleaseRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, original_url: str) -> None:
        self.original_url = original_url

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_redirect_url(newurl, self.original_url)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _urlopen(request, *, timeout: float):
    # Validate every hop before sending any redirected request.
    opener = urllib.request.build_opener(_ReleaseRedirectHandler(request.full_url))
    return opener.open(request, timeout=timeout)


def _validate_response_url(response, original_url: str) -> None:
    final_url = response.geturl() if hasattr(response, "geturl") else original_url
    _validate_redirect_url(final_url, original_url)


def _read_json_response(url: str, timeout: float) -> dict:
    if url != LATEST_RELEASE_URL:
        raise UpdateError("Release URL must use the fixed media repository HTTPS endpoint.")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with _urlopen(request, timeout=timeout) as response:
            _validate_response_url(response, url)
            raw = response.read(MAX_DOWNLOAD_BYTES + 1)
            if len(raw) > MAX_DOWNLOAD_BYTES:
                raise UpdateError("Release metadata exceeds the safety limit.")
    except urllib.error.HTTPError as error:
        raise UpdateError(f"GitHub release lookup failed (HTTP {error.code}).") from error
    except urllib.error.URLError as error:
        raise UpdateError(f"GitHub release lookup failed: {error.reason}") from error
    except OSError as error:
        raise UpdateError(f"GitHub release lookup failed: {error}") from error
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise UpdateError("GitHub release lookup returned invalid JSON.") from error
    if not isinstance(value, dict):
        raise UpdateError("GitHub release lookup returned an unexpected response.")
    return value


def fetch_latest_release(
    *,
    timeout: float = 5.0,
    release_url: str = LATEST_RELEASE_URL,
    asset_name: str = ASSET_NAME,
) -> ReleaseInfo:
    if release_url != LATEST_RELEASE_URL or asset_name != ASSET_NAME:
        raise UpdateError("Only the fixed cloudrelay-mediagen release channel is supported.")
    payload = _read_json_response(release_url, timeout)
    tag_name = str(payload.get("tag_name") or "").strip()
    if not tag_name:
        raise UpdateError("Latest release did not contain tag_name.")
    version = parse_version(tag_name)
    if version.major < 2:
        raise UpdateError("Media releases must use version 2.x or later.")

    assets = payload.get("assets")
    if not isinstance(assets, list):
        assets = []
    asset_url: str | None = None
    asset_digest: str | None = None
    for asset in assets:
        if not isinstance(asset, dict) or asset.get("name") != asset_name:
            continue
        candidate_url = str(asset.get("browser_download_url") or "").strip()
        if candidate_url:
            asset_url = candidate_url
        digest = str(asset.get("digest") or "").strip().lower()
        if digest.startswith("sha256:"):
            asset_digest = digest.removeprefix("sha256:")
        break

    return ReleaseInfo(
        version=version,
        tag_name=tag_name,
        asset_url=asset_url,
        asset_digest=asset_digest,
        published_at=str(payload.get("published_at") or "") or None,
    )


def download_release_asset(
    release: ReleaseInfo,
    *,
    timeout: float = 30.0,
) -> bytes:
    if not release.asset_url:
        raise UpdateError(f"Release {release.tag_name} has no {ASSET_NAME} asset.")
    expected_url = (
        f"https://github.com/{REPOSITORY}/releases/download/"
        f"{quote(release.tag_name, safe='')}/{ASSET_NAME}"
    )
    if release.asset_url != expected_url:
        raise UpdateError("Release asset URL is outside the trusted GitHub repository or tag/asset path.")
    if release.version.major < 2 or parse_version(release.tag_name) != release.version:
        raise UpdateError("Release tag is not a matching media version.")
    if not release.asset_digest or not re.fullmatch(r"[0-9a-f]{64}", release.asset_digest):
        raise UpdateError("Release asset does not provide a usable SHA-256 digest.")

    request = urllib.request.Request(
        release.asset_url,
        headers={"Accept": "application/octet-stream", "User-Agent": USER_AGENT},
    )
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    size = 0
    try:
        with _urlopen(request, timeout=timeout) as response:
            _validate_response_url(response, release.asset_url)
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_DOWNLOAD_BYTES:
                    raise UpdateError("Release asset is larger than the 64 MiB safety limit.")
                digest.update(chunk)
                chunks.append(chunk)
    except urllib.error.HTTPError as error:
        raise UpdateError(f"Release asset download failed (HTTP {error.code}).") from error
    except urllib.error.URLError as error:
        raise UpdateError(f"Release asset download failed: {error.reason}") from error
    except OSError as error:
        raise UpdateError(f"Release asset download failed: {error}") from error

    actual_digest = digest.hexdigest()
    if actual_digest != release.asset_digest:
        raise UpdateError(
            "Release asset SHA-256 mismatch: "
            f"expected {release.asset_digest}, got {actual_digest}."
        )
    return b"".join(chunks)


def _safe_member_path(name: str) -> PurePosixPath:
    normalized = name.replace("\\", "/")
    if normalized.endswith("/"):
        normalized = normalized[:-1]
    parts = normalized.split("/")
    reserved = {"con", "prn", "aux", "nul", "conin$", "conout$"}
    reserved.update(f"{prefix}{number}" for prefix in ("com", "lpt") for number in range(1, 10))
    if (not normalized or any(
            part in ("", ".", "..") or part.endswith((".", " "))
            or any(ord(char) < 32 or char in '<>:"|?*' for char in part)
            or part.split(".")[0].casefold() in reserved
            for part in parts)):
        raise UpdateError(f"Unsafe archive path: {name!r}")
    return PurePosixPath(normalized)


def _archive_root(paths: list[PurePosixPath]) -> PurePosixPath:
    candidates = sorted(
        (path for path in paths if path.name == "SKILL.md"),
        key=lambda path: len(path.parts),
    )
    if not candidates:
        raise UpdateError("Release archive does not contain SKILL.md.")
    if len(candidates) != 1:
        raise UpdateError("Release archive must contain exactly one skill identity.")
    return candidates[0].parent


def _relative_member(path: PurePosixPath, root: PurePosixPath) -> Path | None:
    root_parts = root.parts
    if path.parts[: len(root_parts)] != root_parts:
        return None
    relative_parts = path.parts[len(root_parts) :]
    if not relative_parts:
        return None
    relative = Path(*relative_parts)
    return relative if relative in MANAGED_FILES else None


def _check_member_size(size: int, extracted_bytes: int) -> int:
    if size < 0 or size > MAX_EXTRACTED_BYTES:
        raise UpdateError("Release archive member exceeds the 64 MiB safety limit.")
    total = extracted_bytes + size
    if total > MAX_EXTRACTED_BYTES:
        raise UpdateError("Release archive expands beyond the 64 MiB safety limit.")
    return total


def _write_archive_files(archive: zipfile.ZipFile | tarfile.TarFile, destination: Path) -> None:
    if isinstance(archive, zipfile.ZipFile):
        members = []
        for info in archive.infolist():
            path = _safe_member_path(info.filename)
            mode = stat.S_IFMT(info.external_attr >> 16)
            if info.is_dir():
                if mode not in (0, stat.S_IFDIR):
                    raise UpdateError(f"Non-regular archive entry is not allowed: {info.filename!r}")
                continue
            if mode == stat.S_IFLNK:
                raise UpdateError(f"Symlink entries are not allowed: {info.filename!r}")
            if mode not in (0, stat.S_IFREG):
                raise UpdateError(f"Non-regular archive entry is not allowed: {info.filename!r}")
            members.append((path, info))
        root = _archive_root([path for path, _ in members])
        seen: set[Path] = set()
        extracted_bytes = 0
        for path, info in members:
            relative = _relative_member(path, root)
            if relative is None:
                continue
            if relative in seen:
                raise UpdateError(f"Duplicate archive entry: {relative}")
            seen.add(relative)
            extracted_bytes = _check_member_size(info.file_size, extracted_bytes)
            data = archive.read(info)
            if len(data) != info.file_size:
                raise UpdateError(f"Archive member size changed while reading: {info.filename!r}")
            if extracted_bytes > MAX_EXTRACTED_BYTES:
                raise UpdateError("Release archive expands beyond the 64 MiB safety limit.")
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return

    members = []
    for info in archive.getmembers():
        path = _safe_member_path(info.name)
        if info.isdir():
            continue
        if info.issym() or info.islnk() or not info.isfile():
            raise UpdateError(f"Non-regular archive entry is not allowed: {info.name!r}")
        members.append((path, info))
    root = _archive_root([path for path, _ in members])
    seen = set()
    extracted_bytes = 0
    for path, info in members:
        relative = _relative_member(path, root)
        if relative is None:
            continue
        if relative in seen:
            raise UpdateError(f"Duplicate archive entry: {relative}")
        seen.add(relative)
        extracted_bytes = _check_member_size(info.size, extracted_bytes)
        extracted = archive.extractfile(info)
        if extracted is None:
            raise UpdateError(f"Could not read archive entry: {info.name!r}")
        data = extracted.read()
        if len(data) != info.size:
            raise UpdateError(f"Archive member size changed while reading: {info.name!r}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def extract_release_archive(archive_bytes: bytes, destination: Path) -> None:
    ensure_safe_path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    stream = io.BytesIO(archive_bytes)
    try:
        with zipfile.ZipFile(stream) as archive:
            _write_archive_files(archive, destination)
    except zipfile.BadZipFile:
        stream.seek(0)
        try:
            with tarfile.open(fileobj=stream, mode="r:*") as archive:
                _write_archive_files(archive, destination)
        except (tarfile.TarError, EOFError) as error:
            raise UpdateError("Release asset is not a supported ZIP or tar archive.") from error
    validate_skill_tree(destination)


def validate_skill_identity(skill_dir: Path, expected_name: str = SKILL_NAME) -> str:
    """Validate exact identity before reading state or modifying a tree."""
    ensure_safe_path(skill_dir)
    skill_file = skill_dir / "SKILL.md"
    ensure_safe_path(skill_file)
    if not _is_regular_file(skill_file):
        raise UpdateError("Skill identity requires a regular SKILL.md file.")
    frontmatter = _FRONTMATTER_RE.match(skill_file.read_text(encoding="utf-8"))
    if not frontmatter:
        raise UpdateError("Release SKILL.md has invalid YAML frontmatter boundaries.")
    # Support canonical block-mapping keys only. Regex-only name extraction
    # must not accept alternate YAML keys (escaped, explicit, merge or tagged)
    # that a full YAML consumer could interpret as another identity.
    header = frontmatter.group(1)
    for line in header.splitlines():
        if not line.strip() or line.startswith("#") or line[0].isspace():
            continue
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_-]*:[ \t]*.*$", line):
            raise UpdateError("Skill frontmatter requires canonical unquoted mapping keys.")
    if len(re.findall(r"(?m)^name:", header)) != 1:
        raise UpdateError("SKILL.md requires one canonical top-level name field.")
    names = re.findall(r"(?m)^[ \t]*(?:name|[\"\']name[\"\'])[ \t]*:[ \t]*(.*?)[ \t]*$", frontmatter.group(1))
    if len(names) != 1:
        raise UpdateError("Release SKILL.md must contain exactly one name field.")
    value = names[0].strip()
    if value not in (expected_name, chr(34) + expected_name + chr(34), chr(39) + expected_name + chr(39)):
        raise UpdateError(f"SKILL.md is not the {expected_name} skill.")
    return expected_name


_MIGRATION_ADDITIONS = (Path("scripts/migrate_from_imagegen.py"), Path("references/migration.md"))


def _required_files_for(version: SemanticVersion) -> tuple[Path, ...]:
    # The published v2.0.0 media package predates migration. This exception is
    # file-version compatibility within the media identity, never a legacy fallback.
    if version == parse_version("2.0.0"):
        return tuple(path for path in REQUIRED_FILES if path not in _MIGRATION_ADDITIONS)
    return REQUIRED_FILES


def validate_skill_tree(skill_dir: Path) -> SemanticVersion:
    ensure_safe_path(skill_dir)
    validate_skill_identity(skill_dir)
    version = local_version(skill_dir)
    if version is None:
        raise UpdateError("Release is missing required VERSION file.")
    if version.major < 2:
        raise UpdateError("Media skill VERSION must be 2.x or later.")
    required = _required_files_for(version)
    for relative in MANAGED_FILES:
        ensure_safe_path(skill_dir / relative)
    missing = [str(path) for path in required if not _is_regular_file(skill_dir / path)]
    if missing:
        raise UpdateError("Release is missing required regular file(s): " + ", ".join(missing))
    for relative in MANAGED_FILES:
        path = skill_dir / relative
        if path.exists() and not _is_regular_file(path):
            raise UpdateError(f"Release contains a non-regular file: {relative}")
        if relative.suffix != ".py" or not path.exists():
            continue
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except (OSError, SyntaxError) as error:
            raise UpdateError(f"Release Python validation failed for {path.name}: {error}") from error
    return version


def stage_release(archive_bytes: bytes, parent: Path) -> tuple[Path, SemanticVersion]:
    ensure_safe_path(parent)
    stage = Path(tempfile.mkdtemp(prefix=".cloudrelay-update-", dir=parent))
    try:
        extract_release_archive(archive_bytes, stage)
        return stage, validate_skill_tree(stage)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def _is_link(path: Path) -> bool:
    try:
        metadata = path.lstat()
        if path.is_symlink() or stat.S_ISLNK(metadata.st_mode):
            return True
        is_junction = getattr(path, "is_junction", None)
        if is_junction and is_junction():
            return True
        # Path.is_junction() only exists in Python 3.12+. On supported
        # Windows 3.10/3.11 runtimes, all reparse points are unsafe here.
        reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        return bool(os.name == "nt" and getattr(metadata, "st_file_attributes", 0) & reparse_point)
    except FileNotFoundError:
        return False
    except OSError:
        # An unreadable path must not be treated as safe for a write.
        return True


def _ensure_no_link_components(path: Path) -> None:
    for component in [*reversed(path.parents), path]:
        if _is_link(component):
            raise UpdateError(f"Refusing to write through a symlink or junction: {component}")


def ensure_safe_path(path: Path) -> None:
    """Reject symlink/junction components before a caller reads or writes a path."""
    _ensure_no_link_components(path)


def _is_regular_file(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return False
    return not _is_link(path) and stat.S_ISREG(metadata.st_mode)


def _process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        # Windows does not implement POSIX signal 0 semantics for os.kill.
        # Query a process handle instead; an inaccessible process is treated
        # as alive so a lock is never reclaimed aggressively.
        try:
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
            kernel32.OpenProcess.restype = ctypes.c_void_p
            kernel32.GetExitCodeProcess.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_ulong),
            ]
            kernel32.GetExitCodeProcess.restype = ctypes.c_int
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.restype = ctypes.c_int
            handle = kernel32.OpenProcess(0x00100000 | 0x00001000, False, pid)
            if not handle:
                error_code = ctypes.get_last_error()
                return error_code not in {2, 3, 6, 87}
            exit_code = ctypes.c_ulong()
            try:
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return True
                return exit_code.value == 259  # STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except (AttributeError, OSError):
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _stale_lock(lock: Path) -> bool:
    """Return whether a lock left by a dead process can be reclaimed."""
    try:
        owner = lock / "owner"
        if _is_link(owner):
            return False
        owner_text = owner.read_text(encoding="ascii").strip()
        pid = int(owner_text)
    except (FileNotFoundError, OSError, ValueError):
        try:
            age = time.time() - lock.stat().st_mtime
        except OSError:
            return False
        return age > LOCK_STALE_SECONDS
    return not _process_is_alive(pid)


def _reclaim_stale_lock(lock: Path) -> bool:
    if not _stale_lock(lock):
        return False
    try:
        if _is_link(lock):
            return False
        shutil.rmtree(lock)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


@contextmanager
def _update_lock(target: Path):
    lock = target.parent / f".{target.name}.update.lock"
    _ensure_no_link_components(lock.parent)
    if _is_link(lock):
        raise UpdateError(f"Refusing to use a symlinked update lock: {lock}")
    acquired = False
    for _attempt in range(2):
        try:
            lock.mkdir()
            acquired = True
            break
        except FileExistsError as error:
            if not _reclaim_stale_lock(lock):
                raise UpdateError(f"Another update is already running (lock: {lock}).") from error
    if not acquired:
        raise UpdateError(f"Another update is already running (lock: {lock}).")
    try:
        (lock / "owner").write_text(str(os.getpid()), encoding="ascii")
        yield
    finally:
        try:
            owner = lock / "owner"
            if not _is_link(owner) and owner.read_text(encoding="ascii").strip() == str(os.getpid()):
                shutil.rmtree(lock, ignore_errors=True)
        except (OSError, UnicodeError):
            pass


def apply_staged_release(stage: Path, target: Path) -> None:
    ensure_safe_path(stage)
    _ensure_no_link_components(target)
    if stage == target or stage in target.parents or target in stage.parents:
        raise UpdateError("Staging and installed skill directories must be disjoint.")
    staged_version = validate_skill_tree(stage)
    backup = None
    replaced: list[Path] = []
    try:
        with _update_lock(target):
            validate_skill_identity(target)
            current_version = local_version(target)
            if current_version is not None and compare_versions(staged_version, current_version) < 0:
                raise UpdateError(
                    "Refusing to install a staged release older than the current version: "
                    f"{staged_version.normalized} < {current_version.normalized}."
                )
            backup = Path(tempfile.mkdtemp(prefix=".cloudrelay-backup-", dir=target.parent))
            try:
                for relative in MANAGED_FILES:
                    source = stage / relative
                    if not source.is_file():
                        if relative not in _required_files_for(staged_version):
                            continue  # Preserve any existing optional migration files.
                        raise UpdateError(f"Staged release is missing {relative}.")
                    destination = target / relative
                    _ensure_no_link_components(destination)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    old = backup / relative
                    if destination.exists():
                        if not _is_regular_file(destination):
                            raise UpdateError(f"Refusing to replace a non-regular file: {destination}")
                        old.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(destination, old)
                    with tempfile.NamedTemporaryFile(
                        prefix=f".{destination.name}.",
                        suffix=".new",
                        dir=destination.parent,
                        delete=False,
                    ) as temporary:
                        destination_tmp = Path(temporary.name)
                    try:
                        shutil.copy2(source, destination_tmp)
                        destination_tmp.replace(destination)
                    finally:
                        if destination_tmp.exists():
                            destination_tmp.unlink()
                    replaced.append(relative)
            except Exception as error:
                rollback_errors: list[str] = []
                for relative in reversed(replaced):
                    destination = target / relative
                    old = backup / relative
                    try:
                        _ensure_no_link_components(destination)
                        if old.exists():
                            old.replace(destination)
                        elif destination.exists():
                            destination.unlink()
                    except OSError as rollback_error:
                        rollback_errors.append(f"{relative}: {rollback_error}")
                if rollback_errors:
                    raise UpdateError(
                        "Update failed; rollback was incomplete. "
                        f"Preserved backup: {backup}. Errors: {rollback_errors}"
                    ) from error
                shutil.rmtree(backup, ignore_errors=True)
                backup = None
                raise UpdateError(f"Update failed; rollback completed: {error}") from error
            else:
                shutil.rmtree(backup, ignore_errors=True)
                backup = None
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        if backup is not None and not backup.exists():
            backup = None
