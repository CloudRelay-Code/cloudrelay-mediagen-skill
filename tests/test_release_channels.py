"""Fixed media channel, archive, packaging, and installation regression tests."""
from __future__ import annotations
import contextlib
import hashlib
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
from unittest import mock
from urllib.parse import quote
import warnings
import zipfile
import install
from scripts import _update_common as common
from scripts import check_update, update
from tools import package_skill, sync_adapters

ROOT = Path(__file__).resolve().parent.parent
REPO = "CloudRelay-Code/cloudrelay-mediagen-skill"
ASSET = "cloudrelay-mediagen.skill"
OLD_REPO = "CloudRelay-Code/cloudrelay-imagegen-skill"
OLD_ASSET = "cloudrelay-imagegen.skill"
LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
TAG = f"v{VERSION}"
URL = f"https://github.com/{REPO}/releases/download/{TAG}/{ASSET}"
MEDIA_FILES = {Path(p) for p in (
    "scripts/generate_video.py", "references/images.md", "references/videos.md",
    "scripts/migrate_from_imagegen.py", "references/migration.md",
)}
REJECTIONS = (common.UpdateError, install.InstallError, OSError, ValueError)

class Response:
    def __init__(self, data, url):
        self.stream, self.url = BytesIO(data), url
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def read(self, size=-1): return self.stream.read(size)
    def geturl(self): return self.url

class ReleaseChannelTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        blocker = mock.patch.object(common, "_urlopen", side_effect=AssertionError("Unexpected live network request"))
        blocker.start()
        self.addCleanup(blocker.stop)

    def tree(self, name="media"):
        target = self.root / name
        for relative in package_skill.PACKAGE_FILES:
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, destination)
        return target

    def archive(self, changes=None, missing=(), extras=()):
        changes = changes or {}
        stream = BytesIO()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
                for relative in package_skill.PACKAGE_FILES:
                    name = relative.as_posix()
                    if name not in missing:
                        archive.writestr(name, changes.get(name, (ROOT / relative).read_bytes()))
                for name, data in extras:
                    archive.writestr(name, data)
        return stream.getvalue()

    def release(self, data=b"fixture", url=URL, digest=None, version=VERSION):
        return common.ReleaseInfo(common.parse_version(version), f"v{version}", url,
            hashlib.sha256(data).hexdigest() if digest is None else digest, None)

    def metadata(self, assets=None, version=VERSION):
        if assets is None:
            assets = [{"name": ASSET, "browser_download_url": URL, "digest": "sha256:" + "a" * 64}]
        return json.dumps({"tag_name": f"v{version}", "assets": assets}).encode()

    def snapshot(self, target):
        return {p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob("*") if p.is_file()}

    def legacy(self, target):
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text("---\nname: cloudrelay-imagegen\n---\n", encoding="utf-8")
        (target / "VERSION").write_text("1.5.0\n", encoding="utf-8")
        (target / "custom.txt").write_bytes(b"preserve user data")
        return target

    def test_build_metadata_is_preserved_but_does_not_change_precedence(self):
        left = common.parse_version("v2.0.1-rc.1+release.001")
        right = common.parse_version("2.0.1-rc.1+release.002")
        self.assertEqual(left.normalized, "2.0.1-rc.1+release.001")
        self.assertEqual(left.build, ("release", "001"))
        self.assertNotEqual(left, right)
        self.assertEqual(common.compare_versions(left, right), 0)

    def test_malformed_build_metadata_is_rejected(self):
        for version in ("2.0.1+", "2.0.1+.", "2.0.1+build..1", "2.0.1+.build", "2.0.1+build.", "2.0.1+build_1"):
            with self.subTest(version=version), self.assertRaises(common.UpdateError):
                common.parse_version(version)

    def test_release_version_must_match_tag_build_metadata_exactly(self):
        version = "2.0.2+release.1"
        tag = "v" + version
        url = f"https://github.com/{REPO}/releases/download/{quote(tag, safe='')}/{ASSET}"
        release = common.ReleaseInfo(common.parse_version("2.0.2+release.2"), tag, url, "a" * 64, None)
        with self.assertRaises(common.UpdateError):
            common.download_release_asset(release)
        payload = b"valid build metadata"
        with mock.patch.object(common, "_urlopen", return_value=Response(payload, url)):
            self.assertEqual(common.download_release_asset(self.release(payload, url=url, version=version)), payload)

    def test_archive_build_mismatch_preserves_target_and_cleans_stage(self):
        target = self.tree()
        before = self.snapshot(target)
        tag_version = "2.0.2+release.1"
        with mock.patch.object(update, "fetch_latest_release", return_value=self.release(version=tag_version)):
            with mock.patch.object(update, "download_release_asset", return_value=self.archive({"VERSION": "2.0.2+release.2\n"})):
                with contextlib.redirect_stdout(StringIO()), contextlib.redirect_stderr(StringIO()):
                    self.assertNotEqual(update.main(["--target-dir", str(target), "--auto"]), 0)
        self.assertEqual(self.snapshot(target), before)
        self.assertFalse(list(self.root.glob(".cloudrelay-update-*")))

    def test_only_exact_published_2_0_0_has_optional_migration_files(self):
        additions = ("scripts/migrate_from_imagegen.py", "references/migration.md")
        for version in ("2.0.0+release.1", "2.0.0-rc.1"):
            with self.subTest(version=version), self.assertRaises(common.UpdateError):
                common.stage_release(self.archive({"VERSION": version + "\n"}, missing=additions), self.root)

    def test_fixed_channel_and_requested_endpoint(self):
        self.assertEqual(common.REPOSITORY, REPO)
        self.assertEqual(common.ASSET_NAME, ASSET)
        self.assertEqual(common.LATEST_RELEASE_URL, LATEST)
        with mock.patch.object(common, "_urlopen", return_value=Response(self.metadata(), LATEST)) as opened:
            release = common.fetch_latest_release()
        self.assertEqual(opened.call_args.args[0].full_url, LATEST)
        self.assertEqual(release.asset_url, URL)
        self.assertEqual(release.asset_digest, "a" * 64)

    def test_legacy_overrides_and_versions_are_rejected(self):
        for kwargs in ({"asset_name": OLD_ASSET},
                       {"release_url": f"https://api.github.com/repos/{OLD_REPO}/releases/latest"},
                       {"release_url": LATEST + "?legacy=1"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(common.UpdateError):
                common.fetch_latest_release(**kwargs)
        with mock.patch.object(common, "_urlopen", return_value=Response(self.metadata(version="1.5.0"), LATEST)):
            with self.assertRaises(common.UpdateError): common.fetch_latest_release()

    def test_old_asset_does_not_trigger_fallback(self):
        old = {"name": OLD_ASSET, "browser_download_url": f"https://github.com/{OLD_REPO}/releases/download/v1.5.0/{OLD_ASSET}", "digest": "sha256:" + "b" * 64}
        with mock.patch.object(common, "_urlopen", return_value=Response(self.metadata([old]), LATEST)) as opened:
            release = common.fetch_latest_release()
            self.assertIsNone(release.asset_url)
            with self.assertRaises(common.UpdateError): common.download_release_asset(release)
        self.assertEqual(opened.call_count, 1)

    def test_mixed_metadata_selects_only_media_asset(self):
        assets = [{"name": OLD_ASSET, "browser_download_url": URL.replace(ASSET, OLD_ASSET)},
                  {"name": ASSET, "browser_download_url": URL, "digest": "sha256:" + "a" * 64}]
        with mock.patch.object(common, "_urlopen", return_value=Response(self.metadata(assets), LATEST)):
            self.assertEqual(common.fetch_latest_release().asset_url, URL)

    def test_digest_correct_wrong_and_missing(self):
        payload = b"verified archive fixture"
        with mock.patch.object(common, "_urlopen", return_value=Response(payload, URL)):
            self.assertEqual(common.download_release_asset(self.release(payload)), payload)
        for digest in ("0" * 64, "", "g" * 64, "a" * 63):
            with self.subTest(digest=digest), mock.patch.object(common, "_urlopen", return_value=Response(payload, URL)):
                with self.assertRaises(common.UpdateError): common.download_release_asset(self.release(payload, digest=digest))
        release = common.ReleaseInfo(common.parse_version(VERSION), TAG, URL, None, None)
        with self.assertRaises(common.UpdateError): common.download_release_asset(release)

    def test_download_url_must_match_exact_repository_tag_and_asset(self):
        for url in (URL.replace(REPO, OLD_REPO), URL.replace(ASSET, OLD_ASSET), URL.replace("https:", "http:"),
                    URL + "?download=1", URL + "#fragment", URL + "/extra", URL.replace(f"/{TAG}/", "/v999.0.0/"),
                    URL.replace("github.com/", "github.com.evil.invalid/"), URL.replace("/releases/download/", "/releases/download/../")):
            with self.subTest(url=url), self.assertRaises(common.UpdateError): common.download_release_asset(self.release(url=url))

    def test_redirect_boundary(self):
        payload = b"fixture"
        for host in ("release-assets.githubusercontent.com", "objects.githubusercontent.com", "github-releases.githubusercontent.com"):
            with self.subTest(host=host), mock.patch.object(common, "_urlopen", return_value=Response(payload, f"https://{host}/asset?signature=test")):
                self.assertEqual(common.download_release_asset(self.release()), payload)
        for url in (URL.replace(REPO, OLD_REPO), "https://evil.invalid/asset", "http://release-assets.githubusercontent.com/asset"):
            with self.subTest(url=url), mock.patch.object(common, "_urlopen", return_value=Response(payload, url)):
                with self.assertRaises(common.UpdateError): common.download_release_asset(self.release())
        with mock.patch.object(common, "_urlopen", return_value=Response(self.metadata(), f"https://api.github.com/repos/{OLD_REPO}/releases/latest")):
            with self.assertRaises(common.UpdateError): common.fetch_latest_release()

    def test_each_redirect_is_checked_before_network(self):
        handler = common._ReleaseRedirectHandler(URL)
        request = common.urllib.request.Request(URL)
        for target in ("https://evil.invalid/x", "http://release-assets.githubusercontent.com/x", URL.replace(REPO, OLD_REPO)):
            with self.subTest(target=target), self.assertRaises(common.UpdateError):
                handler.redirect_request(request, None, 302, "Found", {}, target)
        redirect = handler.redirect_request(request, None, 302, "Found", {}, "https://release-assets.githubusercontent.com/asset?test=1")
        self.assertEqual(redirect.full_url, "https://release-assets.githubusercontent.com/asset?test=1")

    def test_required_manifests_and_real_package(self):
        for manifest in (common.MANAGED_FILES, common.REQUIRED_FILES, package_skill.PACKAGE_FILES, sync_adapters.MANAGED_FILES, install.CORE_FILES + install.CODEX_FILES):
            self.assertTrue(MEDIA_FILES.issubset(set(manifest)))
        self.assertEqual((package_skill.SKILL_NAME, package_skill.ASSET_NAME, package_skill.REPOSITORY), ("cloudrelay-mediagen", ASSET, REPO))
        output = package_skill.build_package(ROOT, self.root / "dist")
        self.assertEqual(output.name, ASSET)
        stage, version = common.stage_release(output.read_bytes(), self.root)
        self.assertEqual(version.normalized, VERSION)
        for relative in MEDIA_FILES: self.assertEqual((stage / relative).read_bytes(), (ROOT / relative).read_bytes())
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(set(archive.namelist()), {p.as_posix() for p in package_skill.PACKAGE_FILES})
            self.assertNotIn("name: cloudrelay-imagegen", archive.read("SKILL.md").decode())

    def test_package_is_reproducible_and_digest_is_verifiable(self):
        first = package_skill.build_package(ROOT, self.root / "one").read_bytes()
        second = package_skill.build_package(ROOT, self.root / "two").read_bytes()
        self.assertEqual(first, second)
        with mock.patch.object(common, "_urlopen", return_value=Response(first, URL)):
            self.assertEqual(common.download_release_asset(self.release(first)), first)

    def test_legacy_and_ambiguous_frontmatter_rejected(self):
        for body in ("name: cloudrelay-imagegen", "name: another-skill\n# name: cloudrelay-mediagen",
                     'name: cloudrelay-mediagen\n"name": cloudrelay-imagegen',
                     'name: cloudrelay-mediagen\n"na\\u006de": cloudrelay-imagegen',
                     "name: cloudrelay-mediagen\n? name\n: cloudrelay-imagegen", 'name: "cloudrelay-mediagen'):
            with self.subTest(body=body), self.assertRaises(common.UpdateError):
                common.stage_release(self.archive({"SKILL.md": f"---\n{body}\n---\n"}), self.root)

    def test_archive_version_and_required_file_compatibility(self):
        with self.assertRaises(common.UpdateError): common.stage_release(self.archive({"VERSION": "1.5.0\n"}), self.root)
        additions = ("scripts/migrate_from_imagegen.py", "references/migration.md")
        _, version = common.stage_release(self.archive({"VERSION": "2.0.0\n"}, missing=additions), self.root)
        self.assertEqual(version.normalized, "2.0.0")
        for missing in (("scripts/generate_video.py",), ("references/images.md",), ("references/videos.md",)):
            with self.subTest(missing=missing), self.assertRaises(common.UpdateError):
                common.stage_release(self.archive({"VERSION": "2.0.0\n"}, missing=missing), self.root)
        with self.assertRaises(common.UpdateError):
            common.stage_release(self.archive({"VERSION": "2.0.1\n"}, missing=additions), self.root)

    def test_archive_paths_duplicates_and_nonregular_entries(self):
        for name in ("../outside", "/outside", "C:/outside", "C:outside", "scripts/update.py:stream", "scripts//extra", "./extra", "scripts\\..\\..\\outside", "//server/share/outside"):
            with self.subTest(name=name), self.assertRaises(common.UpdateError):
                common.stage_release(self.archive(extras=((name, "bad"),)), self.root)
        with self.assertRaises(common.UpdateError):
            common.stage_release(self.archive(extras=(("VERSION", VERSION),)), self.root)
        for kind in (stat.S_IFLNK, stat.S_IFIFO, stat.S_IFCHR, stat.S_IFBLK, stat.S_IFSOCK):
            info = zipfile.ZipInfo("special-entry")
            info.create_system = 3
            info.external_attr = (kind | 0o600) << 16
            with self.subTest(kind=kind), self.assertRaises(common.UpdateError):
                common.stage_release(self.archive(extras=((info, "bad"),)), self.root)

    def test_archive_cannot_mix_two_skill_roots(self):
        with self.assertRaises(common.UpdateError):
            common.stage_release(self.archive(extras=(("legacy/SKILL.md", "---\nname: cloudrelay-imagegen\n---\n"),)), self.root)

    def test_apply_preserves_keys_outputs_and_custom_files(self):
        target = self.tree()
        preserved = {f"imagegen-api-key-{p}": b"FAKE_TEST_KEY_DO_NOT_USE" for p in ("gpt", "gemini", "grok")}
        preserved.update({"generated-images/result.png": b"image fixture", "generated-videos/result.mp4": b"video fixture", "custom/user file.txt": b"user fixture"})
        for name, data in preserved.items():
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        stage, _ = common.stage_release(self.archive(), self.root)
        common.apply_staged_release(stage, target)
        for name, data in preserved.items(): self.assertEqual((target / name).read_bytes(), data)

    def test_apply_and_cli_refuse_legacy_target(self):
        target = self.legacy(self.root / "legacy")
        before = self.snapshot(target)
        stage, _ = common.stage_release(self.archive(), self.root)
        with self.assertRaises(common.UpdateError): common.apply_staged_release(stage, target)
        for module, flags in ((update, ["--auto"]), (check_update, ["--json"])):
            with mock.patch.object(module, "fetch_latest_release") as fetch:
                with contextlib.redirect_stdout(StringIO()), contextlib.redirect_stderr(StringIO()):
                    self.assertNotEqual(module.main(["--target-dir", str(target), *flags]), 0)
                fetch.assert_not_called()
        self.assertEqual(self.snapshot(target), before)

    def test_genuine_media_target_uses_new_channel(self):
        target = self.tree()
        for module, flags in ((update, []), (check_update, ["--json"])):
            with mock.patch.object(module, "fetch_latest_release", return_value=self.release()) as fetch:
                with contextlib.redirect_stdout(StringIO()) as output:
                    self.assertEqual(module.main(["--target-dir", str(target), *flags]), 0)
                fetch.assert_called_once()
                self.assertNotIn("local-variant", output.getvalue())

    def test_tag_archive_mismatch_cleans_stage(self):
        target = self.tree()
        before = self.snapshot(target)
        future = f"{int(VERSION.split('.')[0]) + 1}.0.0"
        with mock.patch.object(update, "fetch_latest_release", return_value=self.release(version=future)):
            with mock.patch.object(update, "download_release_asset", return_value=self.archive()):
                with contextlib.redirect_stdout(StringIO()), contextlib.redirect_stderr(StringIO()):
                    self.assertNotEqual(update.main(["--target-dir", str(target), "--auto"]), 0)
        self.assertEqual(self.snapshot(target), before)
        self.assertFalse(list(self.root.glob(".cloudrelay-update-*")))

    def test_builder_refuses_wrong_identity_version_or_channel(self):
        for index, (filename, old, new) in enumerate((
            ("SKILL.md", "name: cloudrelay-mediagen", "name: cloudrelay-imagegen"),
            ("VERSION", VERSION, "1.5.0"),
            ("scripts/_update_common.py", REPO, OLD_REPO),
            ("scripts/_update_common.py", ASSET, OLD_ASSET),
        )):
            with self.subTest(filename=filename, new=new):
                source = self.tree(f"source-{index}")
                path = source / filename
                path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
                output = self.root / f"dist-{index}"
                with self.assertRaises(REJECTIONS): package_skill.build_package(source, output)
                self.assertFalse((output / ASSET).exists())
                self.assertFalse((output / OLD_ASSET).exists())

    def test_installer_refuses_wrong_source_and_target_identity(self):
        source = self.tree("source")
        (source / "SKILL.md").write_text("---\nname: cloudrelay-imagegen\n---\n", encoding="utf-8")
        home = self.root / "home"
        with self.assertRaises(REJECTIONS):
            install.install_client("codex", "user", source_root=source, home=home, project_dir=self.root)
        self.assertFalse(home.exists())
        destination = install.destination_for("codex", "user", home=home, project_dir=self.root)
        self.legacy(destination)
        before = self.snapshot(destination)
        with self.assertRaises(REJECTIONS):
            install.install_client("codex", "user", source_root=ROOT, home=home, project_dir=self.root, force=True)
        self.assertEqual(self.snapshot(destination), before)

    def test_installer_rejects_parent_file_before_any_write(self):
        home = self.root / "home"
        target = install.destination_for("codex", "user", home=home, project_dir=self.root)
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("---\nname: cloudrelay-mediagen\n---\n", encoding="utf-8")
        (target / "VERSION").write_text("2.0.0\n", encoding="utf-8")
        (target / "scripts").write_bytes(b"unrelated user file")
        before = self.snapshot(target)
        with self.assertRaises(REJECTIONS):
            install.install_client("codex", "user", source_root=ROOT, home=home, project_dir=self.root, force=True)
        self.assertEqual(self.snapshot(target), before)

    def test_force_install_preserves_hardlinked_user_file(self):
        home = self.root / "home"
        target = install.install_client("codex", "user", source_root=ROOT, home=home, project_dir=self.root)
        managed = target / "VERSION"
        managed.write_bytes(b"2.0.0\n")
        user = target / "unknown-user-file"
        try: os.link(managed, user)
        except (OSError, NotImplementedError) as error: self.skipTest(f"Hard links unavailable: {error}")
        before = user.read_bytes()
        install.install_client("codex", "user", source_root=ROOT, home=home, project_dir=self.root, force=True)
        self.assertEqual(user.read_bytes(), before)

    def test_parallel_install_preserves_legacy_and_external_keys(self):
        home = self.root / "home"
        legacy = self.legacy(home / ".codex/skills/cloudrelay-imagegen")
        config = home / ".config/cloudrelay"
        config.mkdir(parents=True)
        for provider in ("gpt", "gemini", "grok"):
            (config / f"imagegen-api-key-{provider}").write_bytes(b"FAKE_TEST_KEY_DO_NOT_USE")
        for name in ("generated-images", "generated-videos"):
            (legacy / name).mkdir()
            (legacy / name / "result").write_bytes(b"existing generated file")
        before_legacy, before_keys = self.snapshot(legacy), self.snapshot(config)
        media = install.install_client("codex", "user", source_root=ROOT, home=home, project_dir=self.root)
        common.validate_skill_identity(media)
        common.validate_skill_identity(legacy, expected_name="cloudrelay-imagegen")
        self.assertEqual(self.snapshot(legacy), before_legacy)
        self.assertEqual(self.snapshot(config), before_keys)

    def test_missing_package_file_has_no_cross_channel_fallback(self):
        source = self.tree()
        (source / "scripts/generate_video.py").unlink()
        output = self.root / "dist"
        with self.assertRaises(REJECTIONS): package_skill.build_package(source, output)
        self.assertFalse((output / ASSET).exists())
        self.assertFalse((output / OLD_ASSET).exists())

    def test_reload_guidance_for_all_clients(self):
        self.assertEqual(set(install.CLIENTS), {"codex", "claude-code", "gemini-cli", "openclaw", "cursor"})
        for name, client in install.CLIENTS.items():
            with self.subTest(client=name):
                self.assertRegex(client.reload_hint.lower(), r"restart|reload|new .*session|new .*chat|new task")

if __name__ == "__main__":
    unittest.main()
