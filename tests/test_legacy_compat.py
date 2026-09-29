"""Offline regression tests of the byte-for-byte historical v1.5.0 runtime.

The historical code is intentionally not hardened or rewritten here. Tests
label known old limitations so a passing suite cannot imply stronger safety.
"""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import BytesIO, StringIO
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
import zipfile

import install as media_install
from scripts import configure_api_key as media_credentials


REPOSITORY = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "legacy_v1_5_0"
HISTORICAL_COMMIT = "2f2d6a077dc2acda21fff2896f144a075d9a3f72"
HISTORICAL_TAG_OBJECT = "1a3d213f841cdb93131dfa42f5803473554f9f39"
LEGACY_REPOSITORY = "CloudRelay-Code/cloudrelay-imagegen-skill"
LEGACY_ASSET = "cloudrelay-imagegen.skill"
LEGACY_DOWNLOAD = (
    "https://github.com/CloudRelay-Code/cloudrelay-imagegen-skill/"
    "releases/download/v1.5.0/cloudrelay-imagegen.skill"
)
FIXTURE_SOURCE_PATHS = {
    "SKILL.md", "VERSION", "agents/openai.yaml", "install.py",
    "scripts/configure_api_key.py", "scripts/generate_image.py",
    "scripts/_update_common.py", "scripts/check_update.py", "scripts/update.py",
    "scripts/update.ps1", "scripts/update.sh", "tools/package_skill.py",
}
KEY_FILENAMES = tuple(f"imagegen-api-key-{provider}" for provider in ("gpt", "gemini", "grok"))


def _materialize_fixture(destination: Path) -> None:
    manifest = json.loads((FIXTURE / "provenance.json").read_text(encoding="utf-8"))
    if manifest["source_tag_object"] != HISTORICAL_TAG_OBJECT:
        raise AssertionError("The historical annotated tag object changed.")
    if manifest["source_commit"] != HISTORICAL_COMMIT:
        raise AssertionError("The historical fixture commit changed.")
    if {record["source_path"] for record in manifest["files"]} != FIXTURE_SOURCE_PATHS:
        raise AssertionError("The historical fixture allowlist changed.")
    for record in manifest["files"]:
        source_path = record["source_path"]
        if record["fixture_path"] != source_path + ".fixture":
            raise AssertionError("Fixture storage must not create a discoverable legacy skill.")
        data = (FIXTURE / record["fixture_path"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != record["sha256"] or len(data) != record["size_bytes"]:
            raise AssertionError(f"Historical fixture integrity failure: {source_path}")
        target = destination / source_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*") if path.is_file()
    }


class LegacyCompatibilityTests(unittest.TestCase):
    """Exercise the actual frozen updater, checker, installer, and builder."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.runtime_temporary = tempfile.TemporaryDirectory(prefix="legacy-v1.5.0-runtime-")
        cls.addClassCleanup(cls.runtime_temporary.cleanup)
        cls.runtime = Path(cls.runtime_temporary.name)
        _materialize_fixture(cls.runtime)
        cls.common = _load_module("_legacy_fixture_update_common", cls.runtime / "scripts/_update_common.py")
        with mock.patch.dict(sys.modules, {"_update_common": cls.common}):
            cls.updater = _load_module("_legacy_fixture_update", cls.runtime / "scripts/update.py")
            cls.checker = _load_module("_legacy_fixture_check_update", cls.runtime / "scripts/check_update.py")
        with mock.patch.dict(sys.modules, {"scripts._update_common": cls.common}):
            cls.installer = _load_module("_legacy_fixture_install", cls.runtime / "install.py")
        cls.builder = _load_module("_legacy_fixture_package", cls.runtime / "tools/package_skill.py")
        cls.credentials = _load_module("_legacy_fixture_credentials", cls.runtime / "scripts/configure_api_key.py")
        for name in (
            "_legacy_fixture_update_common", "_legacy_fixture_update", "_legacy_fixture_check_update",
            "_legacy_fixture_install", "_legacy_fixture_package", "_legacy_fixture_credentials",
        ):
            cls.addClassCleanup(sys.modules.pop, name, None)

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="legacy-compat-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        guard = mock.patch("urllib.request.urlopen", side_effect=AssertionError("Network is disabled in compatibility tests."))
        guard.start()
        self.addCleanup(guard.stop)

    def _release(self, payload: bytes, *, digest: str | None = None, url: str = LEGACY_DOWNLOAD, version: str = "1.5.0"):
        return self.common.ReleaseInfo(
            version=self.common.parse_version(version), tag_name="v" + version,
            asset_url=url, asset_digest=hashlib.sha256(payload).hexdigest() if digest is None else digest,
            published_at="2026-08-31T00:00:00Z",
        )

    def _metadata(self, *, asset: str = LEGACY_ASSET, url: str = LEGACY_DOWNLOAD) -> dict:
        # Synthetic protocol data, not claimed to be a captured GitHub response.
        return {
            "tag_name": "v1.5.0", "published_at": "2026-08-31T00:00:00Z",
            "assets": [{"name": asset, "browser_download_url": url, "digest": "sha256:" + "a" * 64}],
        }

    def _archive(self, *, identity: str = "cloudrelay-imagegen", version: str = "1.5.0", omit: Path | None = None, extras: dict[str, bytes] | None = None, modes: dict[str, int] | None = None) -> bytes:
        stream = BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for relative in self.common.REQUIRED_FILES:
                if relative == omit:
                    continue
                data = (self.runtime / relative).read_bytes()
                if relative == Path("SKILL.md"):
                    data = data.replace(b"name: cloudrelay-imagegen", ("name: " + identity).encode("utf-8"), 1)
                elif relative == Path("VERSION"):
                    data = (version + "\n").encode("ascii")
                name = "legacy-runtime/" + relative.as_posix()
                if modes and relative.as_posix() in modes:
                    info = zipfile.ZipInfo(name)
                    info.create_system = 3
                    info.external_attr = modes[relative.as_posix()] << 16
                    archive.writestr(info, data)
                else:
                    archive.writestr(name, data)
            for name, data in (extras or {}).items():
                archive.writestr(name, data)
        return stream.getvalue()

    def _target(self, name: str = "legacy-target", version: str = "1.4.9") -> Path:
        target = self.root / name
        _materialize_fixture(target)
        (target / "VERSION").write_text(version + "\n", encoding="ascii")
        return target

    def _preserved_files(self, root: Path) -> dict[str, bytes]:
        files = {
            "user-not-managed.txt": b"user custom file",
            "generated-images/user-picture.png": b"user image bytes",
            "generated-videos/user-video.mp4": b"user video bytes",
            "scripts/generate_video.py": b"user video runtime must not be managed by legacy",
            "references/videos.md": b"user notes must not be managed by legacy",
        }
        files.update({name: ("synthetic-test-secret-" + name).encode("ascii") for name in KEY_FILENAMES})
        for relative, data in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return files

    def test_fixture_hashes_and_full_commit_provenance(self) -> None:
        manifest = json.loads((FIXTURE / "provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["source_commit"], HISTORICAL_COMMIT)
        self.assertEqual(manifest["source_tag_object"], HISTORICAL_TAG_OBJECT)
        self.assertEqual(manifest["source_tag"], "v1.5.0")
        self.assertEqual(manifest["historical_repository"], LEGACY_REPOSITORY)
        self.assertEqual(len(manifest["files"]), len(FIXTURE_SOURCE_PATHS))
        for record in manifest["files"]:
            with self.subTest(source=record["source_path"]):
                data = (FIXTURE / record["fixture_path"]).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), record["sha256"])
                self.assertEqual(len(data), record["size_bytes"])
                self.assertEqual((self.runtime / record["source_path"]).read_bytes(), data)

    def test_fixture_contains_no_discoverable_skill_or_user_state(self) -> None:
        self.assertFalse(list(FIXTURE.rglob("SKILL.md")))
        self.assertFalse(list(FIXTURE.rglob(".git")))
        self.assertFalse(list(FIXTURE.rglob("generated-images")))
        self.assertFalse(list(FIXTURE.rglob("generated-videos")))
        self.assertFalse(list(FIXTURE.rglob("imagegen-api-key-*")))
        manifest = json.loads((FIXTURE / "provenance.json").read_text(encoding="utf-8"))
        expected = {item["fixture_path"] for item in manifest["files"]} | {"README.md", "provenance.json"}
        self.assertEqual({path.relative_to(FIXTURE).as_posix() for path in FIXTURE.rglob("*") if path.is_file()}, expected)

    def test_fixture_commands_run_outside_git_without_path_executables(self) -> None:
        self.assertFalse((self.runtime / ".git").exists())
        environment = dict(os.environ, PATH="", PYTHONDONTWRITEBYTECODE="1")
        for relative in ("scripts/update.py", "scripts/check_update.py", "install.py", "tools/package_skill.py"):
            with self.subTest(command=relative):
                result = subprocess.run([sys.executable, "-B", str(self.runtime / relative), "--help"], cwd=self.runtime, env=environment, capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("usage:", result.stdout.lower())

    def test_historical_channel_constants_and_managed_file_limits(self) -> None:
        self.assertEqual(self.common.REPOSITORY, LEGACY_REPOSITORY)
        self.assertEqual(self.common.ASSET_NAME, LEGACY_ASSET)
        self.assertEqual(self.common.LATEST_RELEASE_URL, "https://api.github.com/repos/" + LEGACY_REPOSITORY + "/releases/latest")
        self.assertNotIn(Path("scripts/generate_video.py"), self.common.MANAGED_FILES)
        self.assertNotIn(Path("references/videos.md"), self.common.MANAGED_FILES)
        self.assertTrue(all(Path(name) not in self.common.MANAGED_FILES for name in KEY_FILENAMES))

    def test_latest_request_uses_fixed_old_repository_and_asset(self) -> None:
        metadata = self._metadata()
        metadata["assets"].insert(0, {"name": "cloudrelay-mediagen.skill", "browser_download_url": "https://github.com/CloudRelay-Code/cloudrelay-mediagen-skill/releases/download/v2.0.0/cloudrelay-mediagen.skill", "digest": "sha256:" + "b" * 64})
        with mock.patch.object(self.common.urllib.request, "urlopen", return_value=BytesIO(json.dumps(metadata).encode("utf-8"))) as request:
            release = self.common.fetch_latest_release(timeout=1)
        self.assertEqual(request.call_args.args[0].full_url, self.common.LATEST_RELEASE_URL)
        self.assertEqual(release.asset_url, LEGACY_DOWNLOAD)
        self.assertEqual(release.asset_digest, "a" * 64)
        self.assertEqual(release.version.normalized, "1.5.0")

    def test_media_only_release_does_not_switch_legacy_asset_channel(self) -> None:
        metadata = self._metadata(asset="cloudrelay-mediagen.skill")
        with mock.patch.object(self.common.urllib.request, "urlopen", return_value=BytesIO(json.dumps(metadata).encode("utf-8"))):
            release = self.common.fetch_latest_release(timeout=1)
        self.assertIsNone(release.asset_url)
        with self.assertRaisesRegex(self.common.UpdateError, "no cloudrelay-imagegen.skill asset"):
            self.common.download_release_asset(release)

    def test_historical_checker_reports_old_channel_without_writes(self) -> None:
        target = self._target(version="1.5.0")
        before = _snapshot(target)
        output = StringIO()
        with mock.patch.object(self.checker, "fetch_latest_release", return_value=self._release(b"data")), redirect_stdout(output):
            result = self.checker.main(["--target-dir", str(target), "--json"])
        report = json.loads(output.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(report["repository"], LEGACY_REPOSITORY)
        self.assertEqual(report["asset"], LEGACY_ASSET)
        self.assertEqual(report["status"], "up-to-date")
        self.assertEqual(_snapshot(target), before)

    def test_correct_sha256_download_succeeds_at_old_asset_url(self) -> None:
        payload = self._archive()
        with mock.patch.object(self.common.urllib.request, "urlopen", return_value=BytesIO(payload)) as request:
            self.assertEqual(self.common.download_release_asset(self._release(payload)), payload)
        self.assertEqual(request.call_args.args[0].full_url, LEGACY_DOWNLOAD)

    def test_wrong_sha256_download_is_rejected(self) -> None:
        payload = self._archive()
        with mock.patch.object(self.common.urllib.request, "urlopen", return_value=BytesIO(payload)):
            with self.assertRaisesRegex(self.common.UpdateError, "SHA-256 mismatch"):
                self.common.download_release_asset(self._release(payload, digest="0" * 64))

    def test_absent_or_malformed_sha256_is_rejected_before_download(self) -> None:
        for digest in ("", "a" * 63, "g" * 64, "sha256:" + "a" * 64):
            with self.subTest(digest=digest), self.assertRaisesRegex(self.common.UpdateError, "usable SHA-256"):
                self.common.download_release_asset(self._release(b"data", digest=digest))

    def test_renamed_repository_and_other_untrusted_urls_are_rejected(self) -> None:
        bad_urls = (
            LEGACY_DOWNLOAD.replace("cloudrelay-imagegen-skill/", "cloudrelay-mediagen-skill/"),
            LEGACY_DOWNLOAD.replace("https://", "http://"),
            LEGACY_DOWNLOAD.replace("github.com/", "example.com/"),
            LEGACY_DOWNLOAD.replace("CloudRelay-Code/", "another-owner/"),
            LEGACY_DOWNLOAD + "?download=1", LEGACY_DOWNLOAD + "#asset",
        )
        for url in bad_urls:
            with self.subTest(url=url), self.assertRaisesRegex(self.common.UpdateError, "outside the trusted GitHub repository"):
                self.common.download_release_asset(self._release(b"data", url=url))

    def test_archive_accepts_real_legacy_identity(self) -> None:
        stage = self.root / "stage"
        self.common.extract_release_archive(self._archive(), stage)
        self.assertEqual(self.common.validate_skill_tree(stage).normalized, "1.5.0")
        self.assertIn("name: cloudrelay-imagegen", (stage / "SKILL.md").read_text(encoding="utf-8"))

    def test_archive_rejects_media_identity(self) -> None:
        with self.assertRaisesRegex(self.common.UpdateError, "not the cloudrelay-imagegen skill"):
            self.common.extract_release_archive(self._archive(identity="cloudrelay-mediagen"), self.root / "stage")

    def test_archive_rejects_each_missing_required_file(self) -> None:
        for index, relative in enumerate(self.common.REQUIRED_FILES):
            with self.subTest(missing=relative), self.assertRaises(self.common.UpdateError):
                self.common.extract_release_archive(self._archive(omit=relative), self.root / f"stage-{index}")

    def test_archive_rejects_path_traversal_before_any_file_is_written(self) -> None:
        for index, name in enumerate(("../outside.txt", "legacy-runtime/../../outside.txt", "/outside.txt", "legacy-runtime\\..\\outside.txt")):
            stage = self.root / f"stage-{index}"
            with self.subTest(path=name), self.assertRaisesRegex(self.common.UpdateError, "Unsafe archive path"):
                self.common.extract_release_archive(self._archive(extras={name: b"unsafe"}), stage)
            self.assertFalse(any(stage.rglob("*")))
            self.assertFalse((self.root / "outside.txt").exists())

    def test_zip_archive_rejects_symlinks_even_for_unmanaged_entries(self) -> None:
        stream = BytesIO(self._archive())
        with zipfile.ZipFile(stream, "a") as archive:
            info = zipfile.ZipInfo("legacy-runtime/user-link")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "../outside")
        with self.assertRaisesRegex(self.common.UpdateError, "Symlink entries"):
            self.common.extract_release_archive(stream.getvalue(), self.root / "stage")

    def test_tar_archive_rejects_nonregular_entries(self) -> None:
        for index, entry_type in enumerate((tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE)):
            stream = BytesIO()
            with tarfile.open(fileobj=stream, mode="w") as archive:
                info = tarfile.TarInfo("legacy-runtime/nonregular")
                info.type = entry_type
                info.linkname = "../outside"
                archive.addfile(info)
            with self.subTest(entry_type=entry_type), self.assertRaisesRegex(self.common.UpdateError, "Non-regular archive entry"):
                self.common.extract_release_archive(stream.getvalue(), self.root / f"stage-{index}")

    def test_known_legacy_limit_zip_special_modes_are_materialized_as_regular_files(self) -> None:
        # This characterizes an old limitation; it must not be reported as a rejection.
        stage = self.root / "stage"
        self.common.extract_release_archive(self._archive(modes={"scripts/update.py": stat.S_IFIFO | 0o644}), stage)
        self.assertTrue(stat.S_ISREG((stage / "scripts/update.py").stat().st_mode))
        self.assertEqual((stage / "scripts/update.py").read_bytes(), (self.runtime / "scripts/update.py").read_bytes())

    def test_apply_refuses_downgrade_and_preserves_target(self) -> None:
        target = self._target(version="1.5.1")
        before = _snapshot(target)
        stage, _ = self.common.stage_release(self._archive(), self.root)
        with self.assertRaisesRegex(self.common.UpdateError, "older than the current version"):
            self.common.apply_staged_release(stage, target)
        self.assertEqual(_snapshot(target), before)

    def test_cli_refuses_downgrade_before_downloading(self) -> None:
        target = self._target(version="1.5.1")
        before = _snapshot(target)
        output = StringIO()
        with mock.patch.object(self.updater, "fetch_latest_release", return_value=self._release(b"data")), mock.patch.object(self.updater, "download_release_asset") as download, redirect_stdout(output):
            result = self.updater.main(["--target-dir", str(target), "--apply", "--yes", "--force"])
        self.assertEqual(result, 0)
        self.assertIn("refusing downgrade", output.getvalue())
        download.assert_not_called()
        self.assertEqual(_snapshot(target), before)

    def test_cli_rejects_release_tag_and_archive_version_mismatch(self) -> None:
        target = self._target()
        before = _snapshot(target)
        payload = self._archive()
        output, errors = StringIO(), StringIO()
        with mock.patch.object(self.updater, "fetch_latest_release", return_value=self._release(payload, version="1.5.1")), mock.patch.object(self.updater, "download_release_asset", return_value=payload), redirect_stdout(output), redirect_stderr(errors):
            result = self.updater.main(["--target-dir", str(target), "--apply", "--yes"])
        self.assertEqual(result, 1)
        self.assertIn("does not match archive VERSION", errors.getvalue())
        self.assertEqual(_snapshot(target), before)

    def test_apply_overwrites_only_managed_files_and_preserves_all_user_data(self) -> None:
        target = self._target()
        preserved = self._preserved_files(target)
        external = self.root / "shared-config" / "cloudrelay"
        external.mkdir(parents=True)
        for filename in KEY_FILENAMES:
            (external / filename).write_bytes(("external-test-only-" + filename).encode("ascii"))
        external_before = _snapshot(external)
        archive_extras = {"legacy-runtime/" + name: b"must not overwrite user data" for name in preserved}
        stage, _ = self.common.stage_release(self._archive(extras=archive_extras), self.root)
        self.assertEqual({p.relative_to(stage) for p in stage.rglob("*") if p.is_file()}, set(self.common.MANAGED_FILES))
        self.common.apply_staged_release(stage, target)
        for relative in self.common.MANAGED_FILES:
            with self.subTest(managed=relative):
                self.assertEqual((target / relative).read_bytes(), (self.runtime / relative).read_bytes())
        for relative, data in preserved.items():
            with self.subTest(preserved=relative):
                self.assertEqual((target / relative).read_bytes(), data)
        self.assertEqual(_snapshot(external), external_before)
        self.assertFalse(stage.exists())

    def test_cli_applies_legacy_release_and_reports_reload(self) -> None:
        target = self._target()
        payload = self._archive()
        output = StringIO()
        with mock.patch.object(self.updater, "fetch_latest_release", return_value=self._release(payload)), mock.patch.object(self.updater, "download_release_asset", return_value=payload), redirect_stdout(output):
            result = self.updater.main(["--target-dir", str(target), "--apply", "--yes"])
        self.assertEqual(result, 0)
        self.assertEqual(self.common.local_version(target).normalized, "1.5.0")
        self.assertIn("Restart the host agent or start a new task", output.getvalue())

    def test_known_legacy_limit_apply_does_not_validate_target_identity(self) -> None:
        # Deliberately low synthetic VERSION reveals the missing identity check.
        # This is why legacy tools must not be pointed at a media target.
        target = self._target(name="media-target-with-older-version")
        (target / "SKILL.md").write_text("---\nname: cloudrelay-mediagen\n---\n", encoding="utf-8")
        stage, _ = self.common.stage_release(self._archive(), self.root)
        self.common.apply_staged_release(stage, target)
        self.assertIn("name: cloudrelay-imagegen", (target / "SKILL.md").read_text(encoding="utf-8"))

    def test_historical_builder_uses_fixed_legacy_name_and_file_manifest(self) -> None:
        output = self.builder.build_package(self.runtime, self.root / "packages")
        self.assertEqual(output.name, LEGACY_ASSET)
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(set(archive.namelist()), {p.as_posix() for p in self.common.REQUIRED_FILES})
            self.assertIn(b"name: cloudrelay-imagegen", archive.read("SKILL.md"))
            self.assertNotIn(b"name: cloudrelay-mediagen", archive.read("SKILL.md"))
            self.assertEqual(archive.read("VERSION").strip(), b"1.5.0")
            self.assertNotIn("scripts/generate_video.py", archive.namelist())
        self.common.extract_release_archive(output.read_bytes(), self.root / "verified-package")

    def test_current_and_historical_installers_coexist_for_every_client(self) -> None:
        for client in self.installer.CLIENTS:
            with self.subTest(client=client):
                home = self.root / client / "home with spaces"
                project = self.root / client / "project with spaces"
                legacy_target = self.installer.install_client(client, "user", source_root=self.runtime, home=home, project_dir=project)
                legacy_before = _snapshot(legacy_target)
                media_target = media_install.install_client(client, "user", source_root=REPOSITORY, home=home, project_dir=project)
                self.assertNotEqual(legacy_target, media_target)
                self.assertEqual(_snapshot(legacy_target), legacy_before)
                self.assertIn("name: cloudrelay-imagegen", (legacy_target / "SKILL.md").read_text(encoding="utf-8"))
                self.assertIn("name: cloudrelay-mediagen", (media_target / "SKILL.md").read_text(encoding="utf-8"))
                legacy_keep = self._preserved_files(legacy_target)
                media_keep = {name: b"media-user-data" for name in ("user.txt", "generated-images/keep.png", "generated-videos/keep.mp4", *KEY_FILENAMES)}
                for name, data in media_keep.items():
                    path = media_target / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
                media_before = _snapshot(media_target)
                self.installer.install_client(client, "user", source_root=self.runtime, home=home, project_dir=project, force=True)
                self.assertEqual(_snapshot(media_target), media_before)
                legacy_before = _snapshot(legacy_target)
                media_install.install_client(client, "user", source_root=REPOSITORY, home=home, project_dir=project, force=True)
                self.assertEqual(_snapshot(legacy_target), legacy_before)
                for relative, data in legacy_keep.items():
                    self.assertEqual((legacy_target / relative).read_bytes(), data)
                for relative, data in media_keep.items():
                    self.assertEqual((media_target / relative).read_bytes(), data)

    def test_both_installers_report_each_agent_reload_hint(self) -> None:
        expected_fragments = {
            "codex": "Restart Codex or start a new task.",
            "claude-code": "Start a new Claude Code session.",
            "gemini-cli": "Run /skills reload in Gemini CLI, then /skills list.",
            "openclaw": "Start a new OpenClaw session or wait for its skill watcher to reload.",
            "cursor": "Start a new Cursor Agent chat.",
        }
        media_fragments = dict(expected_fragments, **{
            "codex": "Skills reload automatically; restart Codex if the skill is missing.",
            "openclaw": "Wait for the skill watcher and the next Agent turn; restart the Gateway if watching is disabled.",
            "cursor": "Restart Cursor, then confirm the skill is listed.",
        })
        project = self.root / "shared project with spaces"
        for installer in (self.installer, media_install):
            output = StringIO()
            with redirect_stdout(output):
                result = installer.main(["--client", "all", "--scope", "project", "--project-dir", str(project)])
            self.assertEqual(result, 0)
            hints = expected_fragments if installer is self.installer else media_fragments
            for client, hint in hints.items():
                with self.subTest(skill=installer.SKILL_NAME, client=client):
                    self.assertIn(hint, output.getvalue())

    def test_both_identities_resolve_shared_external_key_names_without_reading_keys(self) -> None:
        external = self.root / "external config with spaces"
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(external)}), mock.patch.object(Path, "read_text", side_effect=AssertionError("No key contents may be read in this test.")), mock.patch.object(Path, "read_bytes", side_effect=AssertionError("No key contents may be read in this test.")):
            for provider, filename in zip(("gpt", "gemini", "grok"), KEY_FILENAMES):
                with self.subTest(provider=provider):
                    expected = external / "cloudrelay" / filename
                    self.assertEqual(self.credentials._secret_file(provider), expected)
                    self.assertEqual(media_credentials._secret_file(provider), expected)
                    self.assertEqual(self.credentials.PROVIDER_ENV_NAMES[provider], media_credentials.PROVIDER_ENV_NAMES[provider])


if __name__ == "__main__":
    unittest.main()
