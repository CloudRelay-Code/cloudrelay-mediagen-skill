#!/usr/bin/env python3
"""Shared, network-safe helpers for CloudRelay ImageGen updates."""

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
from urllib.parse import urlparse
import urllib.error
import urllib.request
import zipfile


REPOSITORY = "CloudRelay-Code/cloudrelay-imagegen-skill"
LATEST_RELEASE_URL = (
    "https://api.github.com/repos/"
    f"{REPOSITORY}/releases/latest"
)
ASSET_NAME = "cloudrelay-imagegen.skill"
try:
    _installed_version = (Path(__file__).resolve().parents[1] / "VERSION").read_text(
        encoding="utf-8"
    ).strip()
except OSError:
    _installed_version = "unknown"
USER_AGENT = f"cloudrelay-imagegen-skill-updater/{_installed_version}"
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
    r"(?:\+[0-9A-Za-z.-]+)?$"
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

    @property
    def normalized(self) -> str:
        suffix = "-" + ".".join(self.prerelease) if self.prerelease else ""
        return f"{self.major}.{self.minor}.{self.patch}{suffix}"


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
    try:
        return parse_version(version_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except OSError as error:
        raise UpdateError(f"Could not read {version_file}: {error}") from error


def _read_json_response(url: str, timeout: float) -> dict:
    parsed_url = urlparse(url)
    if (
        parsed_url.scheme != "https"
        or parsed_url.netloc != "api.github.com"
        or not parsed_url.path.startswith("/repos/")
    ):
        raise UpdateError("Release URL must use the fixed api.github.com HTTPS endpoint.")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_DOWNLOAD_BYTES)
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
    payload = _read_json_response(release_url, timeout)
    tag_name = str(payload.get("tag_name") or "").strip()
    if not tag_name:
        raise UpdateError("Latest release did not contain tag_name.")
    version = parse_version(tag_name)

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
    parsed_url = urlparse(release.asset_url)
    if (
        parsed_url.scheme != "https"
        or parsed_url.netloc != "github.com"
        or not parsed_url.path.startswith(
            "/CloudRelay-Code/cloudrelay-imagegen-skill/releases/download/"
        )
        or parsed_url.query
        or parsed_url.fragment
    ):
        raise UpdateError("Release asset URL is outside the trusted GitHub repository.")
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
        with urllib.request.urlopen(request, timeout=timeout) as response:
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
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise UpdateError(f"Unsafe archive path: {name!r}")
    return path


def _archive_root(paths: list[PurePosixPath]) -> PurePosixPath:
    candidates = sorted(
        (path for path in paths if path.name == "SKILL.md"),
        key=lambda path: len(path.parts),
    )
    if not candidates:
        raise UpdateError("Release archive does not contain SKILL.md.")
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
            if info.is_dir():
                continue
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise UpdateError(f"Symlink entries are not allowed: {info.filename!r}")
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


def validate_skill_tree(skill_dir: Path) -> SemanticVersion:
    missing = [str(path) for path in REQUIRED_FILES if not (skill_dir / path).is_file()]
    if missing:
        raise UpdateError("Release is missing required file(s): " + ", ".join(missing))

    skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    frontmatter = _FRONTMATTER_RE.match(skill_text)
    if not frontmatter:
        raise UpdateError("Release SKILL.md has invalid YAML frontmatter boundaries.")
    name_values = re.findall(r"(?m)^name:\s*(.*?)\s*$", frontmatter.group(1))
    if len(name_values) != 1:
        raise UpdateError("Release SKILL.md must contain exactly one name field.")
    name_value = name_values[0].strip().strip("'\"")
    if name_value != "cloudrelay-imagegen":
        raise UpdateError("Release SKILL.md is not the cloudrelay-imagegen skill.")

    version = parse_version((skill_dir / "VERSION").read_text(encoding="utf-8"))
    for path in skill_dir.joinpath("scripts").glob("*.py"):
        try:
            source = path.read_text(encoding="utf-8")
            compile(source, str(path), "exec")
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
    staged_version = validate_skill_tree(stage)
    backup = None
    replaced: list[Path] = []
    try:
        with _update_lock(target):
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
