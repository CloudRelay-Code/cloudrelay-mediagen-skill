#!/usr/bin/env python3
"""Verify published, independent ImageGen/MediaGen channels using public GETs.

All installation exercises run in a disposable directory. Historical v1.5.0
fixture bytes stay unchanged. Media uses the downloaded, tested candidate
runtime with only its temporary VERSION marker set to 2.0.0; this is not a
claim to exercise the original v2.0.0 updater. No generation API is called.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from urllib.parse import urlparse
import urllib.error
import urllib.request
import zipfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/legacy_v1_5_0"
HISTORICAL_COMMIT = "2f2d6a077dc2acda21fff2896f144a075d9a3f72"
HISTORICAL_TAG_OBJECT = "1a3d213f841cdb93131dfa42f5803473554f9f39"
LEGACY_FILES = (
    "SKILL.md", "VERSION", "agents/openai.yaml", "scripts/configure_api_key.py",
    "scripts/generate_image.py", "scripts/_update_common.py", "scripts/check_update.py",
    "scripts/update.py", "scripts/update.ps1", "scripts/update.sh",
)
MEDIA_FILES = LEGACY_FILES + (
    "scripts/generate_video.py", "scripts/migrate_from_imagegen.py",
    "references/images.md", "references/videos.md", "references/migration.md",
)
CDN_HOSTS = frozenset({"release-assets.githubusercontent.com", "objects.githubusercontent.com", "github-releases.githubusercontent.com"})
MAX_BYTES = 64 * 1024 * 1024


class VerificationError(RuntimeError):
    """A verifiable requirement failed; messages contain no credentials."""


def require(condition, message):
    if not condition:
        raise VerificationError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_path(path):
    path = Path(path).expanduser().absolute()
    require(".." not in path.parts, "Parent traversal is not allowed.")
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        require(not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400,
                "Symlink/junction paths are not allowed.")
    return path


class ReleaseRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, original):
        self.original = original

    def redirect_request(self, request, response, code, message, headers, new_url):
        parsed = urlparse(new_url)
        require(self.original.startswith("https://github.com/") and parsed.scheme == "https"
                and parsed.netloc in CDN_HOSTS and not parsed.fragment,
                "GitHub metadata/asset redirect left its fixed release channel.")
        return super().redirect_request(request, response, code, message, headers, new_url)


def public_get(url, timeout, limit=MAX_BYTES):
    require(url.startswith(("https://api.github.com/repos/CloudRelay-Code/", "https://github.com/CloudRelay-Code/")),
            "Only public, fixed GitHub release GETs are permitted.")
    parsed = urlparse(url)
    require(not parsed.query and not parsed.fragment and not parsed.username, "Unexpected public request URL.")
    # An explicit empty proxy handler prevents reading proxy credentials from env.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), ReleaseRedirect(url))
    request = urllib.request.Request(url, headers={"User-Agent": "cloudrelay-release-verifier/1", "Accept": "application/vnd.github+json" if parsed.netloc == "api.github.com" else "application/octet-stream"}, method="GET")
    try:
        with opener.open(request, timeout=timeout) as response:
            final = urlparse(response.geturl())
            require(response.geturl() == url or (parsed.netloc == "github.com" and final.scheme == "https" and final.netloc in CDN_HOSTS and not final.fragment),
                    "Unexpected final GitHub response origin.")
            data = response.read(limit + 1)
    except urllib.error.HTTPError as error:
        raise VerificationError(f"Public GitHub GET returned HTTP {error.code}.") from None
    except (urllib.error.URLError, OSError):
        raise VerificationError("Public GitHub GET failed; no signed URL or credentials were logged.") from None
    require(len(data) <= limit, "GitHub response exceeds the size limit.")
    return data


def json_get(url, timeout):
    try:
        value = json.loads(public_get(url, timeout, 4 * 1024 * 1024))
    except (UnicodeError, json.JSONDecodeError):
        raise VerificationError("GitHub metadata was not valid JSON.") from None
    require(isinstance(value, dict), "GitHub metadata is not an object.")
    return value


def inspect_archive(data, channel):
    expected = set(channel["files"])
    files = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = archive.infolist()
        require(len(members) == len(expected) and {item.filename for item in members} == expected,
                f"{channel['skill']} archive differs from its fixed full manifest.")
        require(sum(item.file_size for item in members) <= MAX_BYTES, "Expanded archive is too large.")
        for item in members:
            require(not item.is_dir() and not item.flag_bits & 1 and stat.S_ISREG(item.external_attr >> 16),
                    "Archive includes an encrypted or non-regular member.")
            files[item.filename] = archive.read(item)
    require(files["VERSION"].decode("ascii").strip() == channel["tag"][1:], "Archive VERSION differs from the exact release tag.")
    skill = files["SKILL.md"].decode("utf-8")
    header = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", skill, re.DOTALL)
    require(header is not None, "Archive SKILL.md lacks canonical frontmatter.")
    for line in header.group(1).splitlines():
        if not line.strip() or line.startswith("#") or line[0].isspace():
            continue
        require(re.match(r"^[A-Za-z_][A-Za-z0-9_-]*:[ \t]*.*$", line) is not None,
                "Archive frontmatter contains a non-canonical mapping key.")
    names = re.findall(r"(?m)^name:[ \t]*(.*?)[ \t]*$", header.group(1))
    require(names == [channel["skill"]], "Archive has the wrong or ambiguous Skill identity.")
    other = "cloudrelay-mediagen" if channel["skill"] == "cloudrelay-imagegen" else "cloudrelay-imagegen"
    require(f"name: {other}" not in skill, "Archive contains the other Skill's frontmatter.")
    for name, contents in files.items():
        if name.endswith(".py"):
            compile(contents.decode("utf-8"), name, "exec")
    updater = ast.parse(files["scripts/_update_common.py"].decode("utf-8"))
    declarations = {}
    for node in updater.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {"SKILL_NAME", "ASSET_NAME", "REPOSITORY", "MANAGED_FILES", "REQUIRED_FILES"}:
                    require(target.id not in declarations, "Updater redeclares a channel binding.")
                    declarations[target.id] = node.value
    for name, value in (("SKILL_NAME", channel["skill"]), ("ASSET_NAME", channel["asset"]), ("REPOSITORY", channel["repository"])):
        node = declarations.get(name)
        require(isinstance(node, ast.Constant) and node.value == value, "Updater literal channel constants do not match its release.")
    for name in ("MANAGED_FILES", "REQUIRED_FILES"):
        node = declarations.get(name)
        require(isinstance(node, ast.Tuple), "Updater manifest is not a fixed tuple.")
        manifest = []
        for item in node.elts:
            require(isinstance(item, ast.Call) and isinstance(item.func, ast.Name) and item.func.id == "Path"
                    and len(item.args) == 1 and isinstance(item.args[0], ast.Constant) and isinstance(item.args[0].value, str),
                    "Updater manifest is not a literal Path tuple.")
            manifest.append(item.args[0].value)
        require(len(manifest) == len(expected) and set(manifest) == expected, "Updater manifest crosses channel boundaries.")
    return files


def verify_channel(channel, timeout):
    base = "https://api.github.com/repos/" + channel["repository"]
    repo = json_get(base, timeout)
    require(repo.get("full_name") == channel["repository"] and type(repo.get("id")) is int and repo["id"] > 0,
            "Repository identity is wrong or redirected.")
    require(repo.get("private") is False, "The verifier only supports public repositories.")
    latest_url, tag_url = base + "/releases/latest", base + "/releases/tags/" + channel["tag"]
    latest, tagged = json_get(latest_url, timeout), json_get(tag_url, timeout)
    for metadata in (latest, tagged):
        require(metadata.get("tag_name") == channel["tag"] and type(metadata.get("id")) is int,
                "Latest/tag metadata is not the exact expected release.")
        require(metadata.get("draft") is False and metadata.get("prerelease") is False,
                "Expected release must be public and stable.")
        require(metadata.get("url") == base + "/releases/" + str(metadata["id"]), "Release API identity does not belong to the exact repository.")
        require(metadata.get("html_url") == "https://github.com/" + channel["repository"] + "/releases/tag/" + channel["tag"], "Release web URL does not belong to the exact repository/tag.")
    require(latest["id"] == tagged["id"], "Latest and exact tag identify different releases.")
    assets = latest.get("assets", [])
    require(isinstance(assets, list), "Release assets are missing.")
    skill_assets = [asset for asset in assets if isinstance(asset, dict) and str(asset.get("name", "")).endswith(".skill")]
    require(len(skill_assets) == 1 and skill_assets[0].get("name") == channel["asset"], "Latest mixes channels or lacks the exact Skill asset.")
    asset = skill_assets[0]
    tagged_assets = [item for item in tagged.get("assets", []) if item.get("name") == channel["asset"]]
    require(len(tagged_assets) == 1 and asset.get("id") == tagged_assets[0].get("id") and asset.get("digest") == tagged_assets[0].get("digest"), "Latest and tag disagree on asset identity/digest.")
    expected_url = "https://github.com/" + channel["repository"] + "/releases/download/" + channel["tag"] + "/" + channel["asset"]
    require(asset.get("browser_download_url") == expected_url, "Release asset browser_download_url is outside the exact historical/media path.")
    require(type(asset.get("id")) is int and asset.get("url") == base + "/releases/assets/" + str(asset["id"]), "Asset API identity differs from its repository.")
    require(asset.get("state") == "uploaded", "Release asset is not uploaded.")
    advertised = asset.get("digest", "")
    require(isinstance(advertised, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", advertised) is not None, "GitHub did not advertise a usable SHA-256 digest.")
    data = public_get(expected_url, timeout)
    require(digest(data) == advertised[7:] and len(data) == asset.get("size"), "Downloaded asset SHA-256 or byte count differs from GitHub metadata.")
    files = inspect_archive(data, channel)
    evidence = {
        "status": "passed", "repository": channel["repository"], "repository_id": repo["id"],
        "repository_api_url": base, "latest_api_url": latest_url, "tag_api_url": tag_url,
        "tag": channel["tag"], "release_id": latest["id"], "published_at": latest.get("published_at"),
        "asset": channel["asset"], "asset_id": asset["id"], "browser_download_url": expected_url,
        "github_digest": advertised, "download_sha256": digest(data), "download_bytes": len(data),
        "skill_name": channel["skill"], "version": channel["tag"][1:],
        "manifest": sorted(files), "runtime_sha256": {name: digest(value) for name, value in sorted(files.items())},
    }
    return files, evidence


def materialize(files, target):
    for name, data in files.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def historical_fixture(target):
    provenance = json.loads((FIXTURE / "provenance.json").read_text(encoding="utf-8"))
    require(provenance.get("source_commit") == HISTORICAL_COMMIT and provenance.get("source_tag_object") == HISTORICAL_TAG_OBJECT
            and provenance.get("source_tag") == "v1.5.0", "Historical fixture provenance changed.")
    records = provenance.get("files", [])
    expected = set(LEGACY_FILES) | {"install.py", "tools/package_skill.py"}
    require(len(records) == len(expected) and {record["source_path"] for record in records} == expected, "Historical fixture manifest changed.")
    files = {}
    for record in records:
        name = record["source_path"]
        require(record["fixture_path"] == name + ".fixture", "Historical fixture path is unsafe.")
        data = safe_path(FIXTURE / record["fixture_path"]).read_bytes()
        require(digest(data) == record["sha256"] and len(data) == record["size_bytes"], "Historical fixture byte integrity failed.")
        files[name] = data
    require(files["VERSION"].strip() == b"1.5.0", "Historical fixture VERSION changed.")
    materialize(files, target)
    return {"source_commit": HISTORICAL_COMMIT, "source_tag_object": HISTORICAL_TAG_OBJECT,
            "source_tag": "v1.5.0", "fixture_sha256": {name: digest(data) for name, data in sorted(files.items())}, "modified": False}


def add_user_files(target):
    files = {
        "generated-images/preserved.png": b"synthetic image preservation sentinel",
        "generated-videos/preserved.mp4": b"synthetic video preservation sentinel",
        "custom-user-file.txt": b"synthetic custom file preservation sentinel",
    }
    files.update({"imagegen-api-key-" + provider: ("SYNTHETIC-NOT-A-KEY-" + provider).encode("ascii") for provider in ("gpt", "gemini", "grok")})
    materialize(files, target)
    return {name: digest(data) for name, data in files.items()}


def snapshot(root):
    return {path.relative_to(root).as_posix(): digest(path.read_bytes()) for path in root.rglob("*") if path.is_file()}


WORKER = r'''
import contextlib, io, json, os, pathlib, re, runpy, sys, urllib.parse, urllib.request
sys.dont_write_bytecode = True
settings = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
sandbox = pathlib.Path(settings["sandbox"]).resolve()
public_urls = {settings["latest_url"], settings["asset_url"]}
cdn_hosts = {"release-assets.githubusercontent.com", "objects.githubusercontent.com", "github-releases.githubusercontent.com"}
requests, cdns = [], set()

def inside(value):
    if isinstance(value, int): return True
    try: pathlib.Path(value).resolve().relative_to(sandbox); return True
    except (ValueError, TypeError): return False

def audit(event, args):
    if event.startswith("winreg."):
        raise RuntimeError("Registry access is forbidden during release verification")
    if event == "open":
        path, mode, flags = args
        if isinstance(path, (str, bytes)) and pathlib.Path(os.fsdecode(path)).name.startswith("imagegen-api-key-"):
            raise RuntimeError("Updater attempted to open a credential file")
        writes = (isinstance(mode, str) and any(c in mode for c in "wax+")) or flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
        if writes and not inside(path): raise RuntimeError("Write outside disposable sandbox blocked")
    if event in {"os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.utime"} and not inside(args[0]):
        raise RuntimeError("Filesystem mutation outside disposable sandbox blocked")
    if event == "os.rename" and not all(inside(path) for path in args[:2]):
        raise RuntimeError("Rename outside disposable sandbox blocked")
    if event in {"os.symlink", "os.link", "subprocess.Popen", "os.system"}:
        raise RuntimeError("Unexpected link/process operation blocked")
    if event == "urllib.Request":
        url, data, headers, method = args
        parsed = urllib.parse.urlparse(url)
        if method != "GET" or data is not None or any(name.lower() in {"authorization", "cookie", "proxy-authorization"} for name in headers):
            raise RuntimeError("Only unauthenticated public GETs are allowed")
        if url in public_urls:
            requests.append(url)
        elif parsed.scheme == "https" and parsed.netloc in cdn_hosts and not parsed.fragment and settings["asset_url"] in requests:
            cdns.add(parsed.hostname)
        else:
            raise RuntimeError("Request outside the exact release channel blocked")

# Empty proxy configuration avoids reading user proxy/credential settings.
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))
sys.addaudithook(audit)
out, err = io.StringIO(), io.StringIO()
status = 1
error_type = None
try:
    target = pathlib.Path(settings["target"])
    sys.path.insert(0, str(target / "scripts"))
    sys.argv = [str(target / "scripts/update.py"), "--target-dir", str(target), "--apply", "--yes", "--timeout", str(settings["timeout"])]
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            runpy.run_path(str(target / "scripts/update.py"), run_name="__main__")
            status = 0
        except SystemExit as result:
            status = result.code if isinstance(result.code, int) else (0 if result.code is None else 1)
except Exception as error:
    error_type = type(error).__name__

def redact(text):
    # Never retain signed CDN URLs or any query string in verifier evidence.
    return re.sub(r"https?://[^\s<>'\"]+", lambda match: match.group(0) if match.group(0) in public_urls else "[redacted-network-url]", text)

report = {"exit_code": status, "error_type": error_type, "public_get_requests": requests,
          "cdn_hosts_only": sorted(cdns), "stdout": redact(out.getvalue()), "stderr": redact(err.getvalue()),
          "transport": "real public HTTPS GETs; no metadata or asset mocks", "isolated_environment": True,
          "credential_file_open_guard": True, "registry_access_guard": True, "writes_restricted_to_temp": True}
pathlib.Path(settings["report"]).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
sys.exit(status)
'''


def child_environment(sandbox):
    home = sandbox / "isolated-home"
    home.mkdir(exist_ok=True)
    environment = {"HOME": str(home), "USERPROFILE": str(home), "XDG_CONFIG_HOME": str(sandbox / "external-config"),
                   "APPDATA": str(home / "AppData/Roaming"), "LOCALAPPDATA": str(home / "AppData/Local"),
                   "TEMP": str(sandbox), "TMP": str(sandbox), "NO_PROXY": "*"}
    # A synthetic non-empty proxy mapping prevents urllib's Windows registry
    # fallback when the media updater constructs its own default opener.
    if os.name == "nt":
        # Query the OS directory, not the user's registry or environment values.
        import ctypes
        value = ctypes.create_unicode_buffer(32768)
        require(ctypes.windll.kernel32.GetWindowsDirectoryW(value, len(value)) > 0, "Cannot locate Windows system directory.")
        environment["SystemRoot"] = value.value
    return environment


def run_update(sandbox, target, channel, timeout):
    worker = sandbox / "release-verification-worker.py"
    worker.write_text(WORKER, encoding="utf-8")
    result_path = sandbox / (channel["skill"] + "-worker-result.json")
    settings_path = sandbox / (channel["skill"] + "-worker-settings.json")
    latest_url = "https://api.github.com/repos/" + channel["repository"] + "/releases/latest"
    asset_url = "https://github.com/" + channel["repository"] + "/releases/download/" + channel["tag"] + "/" + channel["asset"]
    settings_path.write_text(json.dumps({"sandbox": str(sandbox), "target": str(target), "report": str(result_path),
                                       "latest_url": latest_url, "asset_url": asset_url, "timeout": timeout}), encoding="utf-8")
    result = subprocess.run([sys.executable, "-I", "-B", "-X", "utf8", str(worker), str(settings_path)],
                            cwd=sandbox, env=child_environment(sandbox), capture_output=True, text=True,
                            encoding="utf-8", timeout=timeout * 5 + 60)
    require(result_path.is_file(), "Isolated updater did not produce a verification result.")
    evidence = json.loads(result_path.read_text(encoding="utf-8"))
    evidence["process_exit_code"] = result.returncode
    evidence["status"] = "passed" if result.returncode == 0 and evidence["exit_code"] == 0 else "failed"
    evidence["required_public_gets_seen"] = latest_url in evidence["public_get_requests"] and asset_url in evidence["public_get_requests"]
    return evidence


def exercises(sandbox, channels, payloads, evidence, timeout):
    legacy_target, media_target = sandbox / "legacy_target", sandbox / "media_target"
    evidence["historical_fixture"] = historical_fixture(legacy_target)
    evidence["media_baseline_method"] = "Downloaded published media candidate runtime with temporary VERSION marker set to 2.0.0; original historical v2.0.0 updater is not exercised."
    materialize(payloads["media"], media_target)
    (media_target / "VERSION").write_text("2.0.0\n", encoding="ascii")
    kept = {"legacy": add_user_files(legacy_target), "media": add_user_files(media_target)}
    external = sandbox / "external-config/cloudrelay"
    materialize({"imagegen-api-key-" + provider: ("SYNTHETIC-EXTERNAL-NOT-A-KEY-" + provider).encode("ascii") for provider in ("gpt", "gemini", "grok")}, external)
    external_before, media_before = snapshot(external), snapshot(media_target)
    for key, target, neighbor, neighbor_before in (
        ("legacy", legacy_target, media_target, media_before),
        ("media", media_target, legacy_target, None),
    ):
        if neighbor_before is None:
            neighbor_before = snapshot(neighbor)
        result = run_update(sandbox, target, channels[key], timeout)
        evidence[key + "_update"] = result
        require(result["required_public_gets_seen"], key + " updater did not actually fetch the expected online latest and asset.")
        require(result["status"] == "passed", key + " updater execution failed.")
        require((target / "VERSION").read_text(encoding="ascii").strip() == channels[key]["tag"][1:], key + " updater did not install the expected VERSION.")
        actual = {name: digest((target / name).read_bytes()) for name in channels[key]["files"]}
        require(actual == {name: digest(value) for name, value in payloads[key].items()}, key + " installed runtime differs from the actual downloaded release.")
        require(all(digest((target / name).read_bytes()) == value for name, value in kept[key].items()), key + " update changed a synthetic key/output/custom file.")
        require(snapshot(neighbor) == neighbor_before, key + " update changed the adjacent Skill installation.")
        require(snapshot(external) == external_before, key + " update changed the external synthetic key store.")
        result.update({"installed_version": channels[key]["tag"][1:], "installed_runtime_sha256": actual,
                       "preserved_user_files": sorted(kept[key]), "adjacent_skill_unchanged": True,
                       "external_synthetic_keys_unchanged": True})
    evidence["media_update"]["baseline_method"] = "Downloaded published media candidate runtime, extracted into disposable media_target; only its VERSION was set to 2.0.0. This exercises the tested candidate updater, not the historical v2.0.0 updater."


def write_report(path, report):
    path = safe_path(path)
    require(path.suffix.lower() == ".json", "--output must name a JSON file.")
    require(not path.exists() or path.is_file(), "Report output is not a regular file.")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".release-verification-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        safe_path(path)
        os.replace(name, path)
    finally:
        if Path(name).exists():
            Path(name).unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, required=True, help="Explicit JSON evidence path; written atomically after the attempt.")
    parser.add_argument("--legacy-tag", default="v1.5.1", help="Exact expected stable legacy latest (default: v1.5.1).")
    parser.add_argument("--media-tag", default="v2.0.1", help="Exact expected stable media latest (default: v2.0.1).")
    parser.add_argument("--timeout", type=float, default=30.0, help="Timeout per public GET in seconds (default: 30).")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"v1\.5\.(?:0|[1-9][0-9]*)", args.legacy_tag) or not re.fullmatch(r"v(?:[2-9]|[1-9][0-9]+)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", args.media_tag):
        parser.error("Expected stable tags must remain in legacy 1.5.x and media 2.x-or-later channels.")
    if not 1 <= args.timeout <= 300:
        parser.error("--timeout must be between 1 and 300 seconds.")
    output = safe_path(args.output)
    require(output.suffix.lower() == ".json", "--output must name a JSON file.")
    channels = {
        "legacy": {"repository": "CloudRelay-Code/cloudrelay-imagegen-skill", "skill": "cloudrelay-imagegen", "asset": "cloudrelay-imagegen.skill", "tag": args.legacy_tag, "files": LEGACY_FILES},
        "media": {"repository": "CloudRelay-Code/cloudrelay-mediagen-skill", "skill": "cloudrelay-mediagen", "asset": "cloudrelay-mediagen.skill", "tag": args.media_tag, "files": MEDIA_FILES},
    }
    report = {"schema_version": 1, "status": "failed", "started_at_utc": datetime.now(timezone.utc).isoformat(),
              "expected_tags": {key: channel["tag"] for key, channel in channels.items()}, "channels": {},
              "scope": "Real public GitHub GETs and disposable installations only; no generation API, publishing, user keys or user configuration.",
              "online_success_claimed_only_after_all_checks": True}
    phase = "repository and release verification"
    try:
        payloads = {}
        for key, channel in channels.items():
            phase = key + " public GitHub channel verification"
            payloads[key], report["channels"][key] = verify_channel(channel, args.timeout)
        require(report["channels"]["legacy"]["repository_id"] != report["channels"]["media"]["repository_id"], "Old and new names resolve to the same repository ID.")
        report["distinct_repository_ids"] = True
        phase = "disposable actual-updater exercises"
        with tempfile.TemporaryDirectory(prefix="cloudrelay-online-verification-") as temporary:
            exercises(Path(temporary), channels, payloads, report, args.timeout)
        report["status"] = "passed"
    except Exception as error:
        report["failure"] = {"phase": phase, "type": type(error).__name__,
                             "reason": str(error) if isinstance(error, VerificationError) else "Verification stopped before success; no external URL or exception payload retained."}
    report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    write_report(output, report)
    print(json.dumps({"status": report["status"], "output": str(output), "expected_tags": report["expected_tags"]}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
