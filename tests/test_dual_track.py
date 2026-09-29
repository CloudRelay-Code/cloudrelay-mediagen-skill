"""Offline tests for independent legacy/media build and update tracks.

Release JSON below is synthetic protocol input. It is never evidence that the
old repository or a v1.5.1 GitHub release currently exists. Historical fixture
bytes are integrity checked, materialized unchanged, and never rewritten.
"""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import BytesIO, StringIO
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

from scripts import _update_common as media_common
from tools import package_legacy_skill as legacy_builder, package_skill as media_builder

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "legacy" / "cloudrelay-imagegen"
LEGACY_VERSION = "1.5.1"
LEGACY_URL = "https://github.com/CloudRelay-Code/cloudrelay-imagegen-skill/releases/download/v1.5.1/cloudrelay-imagegen.skill"
LEGACY_LATEST = "https://api.github.com/repos/CloudRelay-Code/cloudrelay-imagegen-skill/releases/latest"
ORIGINAL_TEN = tuple(Path(name) for name in (
    "SKILL.md", "VERSION", "agents/openai.yaml", "scripts/configure_api_key.py",
    "scripts/generate_image.py", "scripts/_update_common.py", "scripts/check_update.py",
    "scripts/update.py", "scripts/update.ps1", "scripts/update.sh",
))


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


class Response:
    def __init__(self, data, url):
        self.stream, self.url = BytesIO(data), url
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def read(self, size=-1): return self.stream.read(size)
    def geturl(self): return self.url


class DualTrackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix="dual-track-runtime-")
        cls.addClassCleanup(temporary.cleanup)
        cls.runtime = Path(temporary.name)
        cls.helpers = load_module("_dual_historical_helpers", ROOT / "tests/test_legacy_compat.py")
        cls.helpers._materialize_fixture(cls.runtime)
        cls.original = load_module("_dual_original_common", cls.runtime / "scripts/_update_common.py")
        with mock.patch.dict(sys.modules, {"_update_common": cls.original}):
            cls.original_updater = load_module("_dual_original_updater", cls.runtime / "scripts/update.py")
        cls.legacy = load_module("_dual_legacy_common", LEGACY / "scripts/_update_common.py")
        with mock.patch.dict(sys.modules, {"_update_common": cls.legacy}):
            cls.legacy_updater = load_module("_dual_legacy_updater", LEGACY / "scripts/update.py")
        for name in ("_dual_historical_helpers", "_dual_original_common", "_dual_original_updater", "_dual_legacy_common", "_dual_legacy_updater"):
            cls.addClassCleanup(sys.modules.pop, name, None)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="dual-track-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for target in ("urllib.request.urlopen",):
            blocker = mock.patch(target, side_effect=AssertionError("Live network is disabled in dual-track tests"))
            blocker.start()
            self.addCleanup(blocker.stop)
        for common in (self.legacy, media_common):
            blocker = mock.patch.object(common, "_urlopen", side_effect=AssertionError("Live network is disabled in dual-track tests"))
            blocker.start()
            self.addCleanup(blocker.stop)

    def tree(self, legacy=True, name=None):
        source = LEGACY if legacy else ROOT
        files = legacy_builder.PACKAGE_FILES if legacy else media_builder.PACKAGE_FILES
        destination = self.root / (name or ("legacy source" if legacy else "media source"))
        for relative in files:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((source / relative).read_bytes())
        return destination

    def package(self, legacy=True):
        builder, source = (legacy_builder, LEGACY) if legacy else (media_builder, ROOT)
        return builder.build_package(source, self.root / ("old-dist" if legacy else "new-dist")).read_bytes()

    def metadata(self, data, version=LEGACY_VERSION, asset=None, url=None):
        return {"tag_name": "v" + version, "assets": [{
            "name": asset or legacy_builder.ASSET_NAME,
            "browser_download_url": url or LEGACY_URL,
            "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
        }]}

    def release(self, data, version=LEGACY_VERSION, url=LEGACY_URL, digest=None):
        return self.legacy.ReleaseInfo(self.legacy.parse_version(version), "v" + version, url,
            hashlib.sha256(data).hexdigest() if digest is None else digest, None)

    def preserve(self, root):
        values = {
            "generated-images/keep.png": b"synthetic image bytes",
            "generated-videos/keep.mp4": b"synthetic video bytes",
            "user-custom.txt": b"user text",
            "scripts/generate_video.py": b"user-owned video helper",
            "references/videos.md": b"user-owned notes",
        }
        values.update({"imagegen-api-key-" + provider: ("FAKE-TEST-" + provider).encode() for provider in ("gpt", "gemini", "grok")})
        for relative, data in values.items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return values

    def rewrite_archive(self, changes=None, omit=(), extras=()):
        changes = changes or {}
        stream = BytesIO()
        with zipfile.ZipFile(BytesIO(self.package())) as original, zipfile.ZipFile(stream, "w") as archive:
            for info in original.infolist():
                if info.filename not in omit:
                    archive.writestr(info, changes.get(info.filename, original.read(info)))
            for name, data in extras:
                archive.writestr(name, data)
        return stream.getvalue()

    def test_fixed_independent_manifests_match_original_ten(self):
        self.assertEqual(legacy_builder.DEFAULT_SOURCE_ROOT, LEGACY)
        self.assertEqual(legacy_builder.PACKAGE_FILES, ORIGINAL_TEN)
        self.assertEqual(tuple(self.original.MANAGED_FILES), ORIGINAL_TEN)
        self.assertEqual(tuple(self.original.REQUIRED_FILES), ORIGINAL_TEN)
        self.assertEqual(tuple(self.legacy.MANAGED_FILES), ORIGINAL_TEN)
        self.assertEqual(tuple(self.legacy.REQUIRED_FILES), ORIGINAL_TEN)
        for module, expected in ((legacy_builder, ("cloudrelay-imagegen", "cloudrelay-imagegen.skill", "CloudRelay-Code/cloudrelay-imagegen-skill")),
                                  (media_builder, ("cloudrelay-mediagen", "cloudrelay-mediagen.skill", "CloudRelay-Code/cloudrelay-mediagen-skill"))):
            self.assertEqual((module.SKILL_NAME, module.ASSET_NAME, module.REPOSITORY), expected)
        self.assertEqual((LEGACY / "VERSION").read_text().strip(), LEGACY_VERSION)
        self.assertGreaterEqual(int((ROOT / "VERSION").read_text().split(".")[0]), 2)

    def test_archives_have_disjoint_identities_and_fixed_runtime_manifests(self):
        for builder, source in ((legacy_builder, LEGACY), (media_builder, ROOT)):
            with self.subTest(identity=builder.SKILL_NAME):
                output = builder.build_package(source, self.root / builder.SKILL_NAME)
                self.assertEqual(output.name, builder.ASSET_NAME)
                with zipfile.ZipFile(output) as archive:
                    self.assertEqual(archive.namelist(), [p.as_posix() for p in builder.PACKAGE_FILES])
                    self.assertIn("name: " + builder.SKILL_NAME, archive.read("SKILL.md").decode())
                    other = "cloudrelay-mediagen" if builder is legacy_builder else "cloudrelay-imagegen"
                    self.assertNotIn("name: " + other, archive.read("SKILL.md").decode())
                    self.assertEqual(archive.read("VERSION"), (source / "VERSION").read_bytes())
        self.assertNotIn(Path("scripts/generate_video.py"), legacy_builder.PACKAGE_FILES)
        self.assertFalse(any(p.parts[0] == "references" for p in legacy_builder.PACKAGE_FILES))
        self.assertIn(Path("scripts/generate_video.py"), media_builder.PACKAGE_FILES)
        self.assertIn(Path("references/videos.md"), media_builder.PACKAGE_FILES)

    def test_both_archives_are_deterministic_and_digest_downloadable(self):
        for builder, source, common in ((legacy_builder, LEGACY, self.legacy), (media_builder, ROOT, media_common)):
            with self.subTest(identity=builder.SKILL_NAME):
                first = builder.build_package(source, self.root / "first").read_bytes()
                second = builder.build_package(source, self.root / "second").read_bytes()
                self.assertEqual(first, second)
                version = (source / "VERSION").read_text().strip()
                url = f"https://github.com/{builder.REPOSITORY}/releases/download/v{version}/{builder.ASSET_NAME}"
                release = common.ReleaseInfo(common.parse_version(version), "v" + version, url, hashlib.sha256(first).hexdigest(), None)
                with mock.patch.object(common, "_urlopen", return_value=Response(first, url)):
                    self.assertEqual(common.download_release_asset(release), first)

    def test_each_builder_rejects_other_source_without_fallback(self):
        for builder, wrong_source in ((legacy_builder, ROOT), (media_builder, LEGACY)):
            with self.subTest(builder=builder.SKILL_NAME):
                output = self.root / builder.SKILL_NAME
                with self.assertRaises((legacy_builder.PackageError, media_common.UpdateError, OSError)):
                    builder.build_package(wrong_source, output)
                self.assertFalse(output.exists())

    def test_missing_legacy_file_never_creates_either_asset(self):
        for index, relative in enumerate(ORIGINAL_TEN):
            with self.subTest(missing=relative):
                source = self.tree(name=f"missing-{index}")
                (source / relative).unlink()
                output = self.root / f"missing-dist-{index}"
                with self.assertRaises(legacy_builder.PackageError):
                    legacy_builder.build_package(source, output)
                self.assertFalse(output.exists())

    def test_missing_media_file_never_falls_back_to_legacy(self):
        source = self.tree(legacy=False)
        (source / "scripts/generate_video.py").unlink()
        with self.assertRaises((media_common.UpdateError, OSError)):
            media_builder.build_package(source, self.root / "dist")
        self.assertFalse((self.root / "dist").exists())

    def test_legacy_builder_rejects_wrong_identity_version_and_channel_literals(self):
        cases = (("SKILL.md", b"name: cloudrelay-imagegen", b"name: cloudrelay-mediagen"),
                 ("VERSION", b"1.5.1", b"2.0.1"),
                 ("VERSION", b"1.5.1", b"1.6.0"),
                 ("scripts/_update_common.py", b'SKILL_NAME = "cloudrelay-imagegen"', b'SKILL_NAME = "cloudrelay-mediagen"'),
                 ("scripts/_update_common.py", b'ASSET_NAME = "cloudrelay-imagegen.skill"', b'ASSET_NAME = "cloudrelay-mediagen.skill"'),
                 ("scripts/_update_common.py", b'REPOSITORY = "CloudRelay-Code/cloudrelay-imagegen-skill"', b'REPOSITORY = "CloudRelay-Code/cloudrelay-mediagen-skill"'))
        for index, (relative, before, after) in enumerate(cases):
            with self.subTest(case=index):
                source = self.tree(name=f"wrong-{index}")
                target = source / relative
                original = target.read_bytes()
                self.assertIn(before, original)
                target.write_bytes(original.replace(before, after, 1))
                output = self.root / f"wrong-dist-{index}"
                with self.assertRaises(legacy_builder.PackageError):
                    legacy_builder.build_package(source, output)
                self.assertFalse(output.exists())

    def test_legacy_builder_rejects_dynamic_or_expanded_manifests(self):
        for index, appended in enumerate(('\nREPOSITORY = "CloudRelay-Code/cloudrelay-imagegen-skill"\n', '\nMANAGED_FILES += (Path("scripts/generate_video.py"),)\n')):
            source = self.tree(name=f"bindings-{index}")
            updater = source / "scripts/_update_common.py"
            updater.write_text(updater.read_text() + appended, encoding="utf-8")
            with self.assertRaises(legacy_builder.PackageError):
                legacy_builder.build_package(source, self.root / f"bindings-dist-{index}")

    def test_legacy_builder_parses_but_never_executes_source(self):
        source = self.tree()
        marker = self.root / "source-executed"
        updater = source / "scripts/_update_common.py"
        updater.write_text(updater.read_text() + f'\nPath({str(marker)!r}).write_text("executed")\n', encoding="utf-8")
        legacy_builder.build_package(source, self.root / "dist")
        self.assertFalse(marker.exists())

    def test_legacy_builder_rejects_non_regular_source_and_output(self):
        source = self.tree()
        missing = source / "VERSION"
        missing.unlink()
        missing.mkdir()
        with self.assertRaises(legacy_builder.PackageError):
            legacy_builder.build_package(source, self.root / "dist")
        output = self.root / "output"
        (output / legacy_builder.ASSET_NAME).mkdir(parents=True)
        with self.assertRaises(legacy_builder.PackageError):
            legacy_builder.build_package(LEGACY, output)

    def test_legacy_builder_rejects_symlink_source_and_output(self):
        source = self.tree()
        link = self.root / "source-link"
        try:
            link.symlink_to(source, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Symlinks unavailable: {error}")
        with self.assertRaises(legacy_builder.PackageError):
            legacy_builder.build_package(link, self.root / "dist")
        output = self.root / "output"
        output.mkdir()
        (output / legacy_builder.ASSET_NAME).symlink_to(source / "VERSION")
        with self.assertRaises(legacy_builder.PackageError):
            legacy_builder.build_package(source, output)

    def test_legacy_builder_rejects_source_file_symlink(self):
        source = self.tree()
        target = source / "VERSION"
        target.unlink()
        try:
            target.symlink_to(LEGACY / "VERSION")
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Symlinks unavailable: {error}")
        with self.assertRaises(legacy_builder.PackageError):
            legacy_builder.build_package(source, self.root / "dist")

    @unittest.skipUnless(os.name == "nt", "Windows junction behavior")
    def test_legacy_builder_rejects_junction_before_source_read(self):
        source = self.tree()
        junction = self.root / "source-junction"
        result = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(source)], capture_output=True, text=True)
        if result.returncode:
            self.skipTest("Junction creation unavailable: " + result.stderr)
        self.addCleanup(lambda: junction.rmdir() if junction.exists() else None)
        with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("Unsafe source read")):
            with self.assertRaises(legacy_builder.PackageError):
                legacy_builder.build_package(junction, self.root / "dist")

    def test_atomic_failure_preserves_prior_asset_and_cleans_temporary(self):
        output = self.root / "dist"
        output.mkdir()
        artifact = output / legacy_builder.ASSET_NAME
        artifact.write_bytes(b"previous accepted artifact")
        with mock.patch.object(legacy_builder.os, "replace", side_effect=OSError("simulated atomic replacement failure")):
            with self.assertRaises(OSError):
                legacy_builder.build_package(LEGACY, output)
        self.assertEqual(artifact.read_bytes(), b"previous accepted artifact")
        self.assertEqual([p.name for p in output.iterdir()], [legacy_builder.ASSET_NAME])

    def test_original_updater_fetches_downloads_stages_and_applies_candidate(self):
        payload = self.package()
        target = self.root / "legacy_target"
        self.helpers._materialize_fixture(target)
        preserved = self.preserve(target)
        media = self.tree(legacy=False, name="media_target")
        self.preserve(media)
        media_before = snapshot(media)
        external = self.root / "external key storage"
        external.mkdir()
        for provider in ("gpt", "gemini", "grok"):
            (external / ("imagegen-api-key-" + provider)).write_bytes(b"FAKE-EXTERNAL-KEY")
        external_before = snapshot(external)
        metadata = json.dumps(self.metadata(payload)).encode()
        output = StringIO()
        with mock.patch.object(self.original.urllib.request, "urlopen", side_effect=[Response(metadata, LEGACY_LATEST), Response(payload, LEGACY_URL)]) as request, \
             mock.patch.object(self.original_updater, "stage_release", wraps=self.original.stage_release) as stage, \
             mock.patch.object(self.original_updater, "apply_staged_release", wraps=self.original.apply_staged_release) as apply, \
             redirect_stdout(output):
            status = self.original_updater.main(["--target-dir", str(target), "--apply", "--yes"])
        self.assertEqual(status, 0, output.getvalue())
        self.assertEqual([call.args[0].full_url for call in request.call_args_list], [LEGACY_LATEST, LEGACY_URL])
        stage.assert_called_once()
        apply.assert_called_once()
        self.assertEqual((target / "VERSION").read_text().strip(), LEGACY_VERSION)
        for relative in ORIGINAL_TEN:
            self.assertEqual((target / relative).read_bytes(), (LEGACY / relative).read_bytes())
        for relative, value in preserved.items():
            self.assertEqual((target / relative).read_bytes(), value)
        self.assertEqual(snapshot(media), media_before)
        self.assertEqual(snapshot(external), external_before)
        upgraded = load_module("_dual_applied_common", target / "scripts/_update_common.py")
        self.addCleanup(sys.modules.pop, "_dual_applied_common", None)
        self.assertEqual(upgraded.SKILL_NAME, "cloudrelay-imagegen")
        self.assertEqual(upgraded.MANAGED_FILES, ORIGINAL_TEN)
        self.assertRegex(output.getvalue(), r"Restart|reload")
        # Execute the installed hardened code against the neighbouring media tree.
        with self.assertRaises(upgraded.UpdateError):
            upgraded.apply_staged_release(self.tree(name="upgraded disposable stage"), media)
        self.assertEqual(snapshot(media), media_before)

    def test_original_updater_rejects_wrong_candidate_digest_without_writes(self):
        payload = self.package()
        target = self.root / "legacy_target"
        self.helpers._materialize_fixture(target)
        self.preserve(target)
        before = snapshot(target)
        metadata = self.metadata(payload)
        metadata["assets"][0]["digest"] = "sha256:" + "0" * 64
        with mock.patch.object(self.original.urllib.request, "urlopen", side_effect=[Response(json.dumps(metadata).encode(), LEGACY_LATEST), Response(payload, LEGACY_URL)]), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            result = self.original_updater.main(["--target-dir", str(target), "--apply", "--yes"])
        self.assertEqual(result, 1)
        self.assertEqual(snapshot(target), before)

    def test_hardened_legacy_requests_only_old_metadata_and_asset(self):
        payload = self.package()
        metadata = self.metadata(payload)
        metadata["assets"].insert(0, {"name": media_builder.ASSET_NAME, "browser_download_url": "https://github.com/CloudRelay-Code/cloudrelay-mediagen-skill/releases/download/v2.0.1/cloudrelay-mediagen.skill"})
        with mock.patch.object(self.legacy, "_urlopen", return_value=Response(json.dumps(metadata).encode(), LEGACY_LATEST)) as request:
            release = self.legacy.fetch_latest_release()
        self.assertEqual(request.call_args.args[0].full_url, LEGACY_LATEST)
        self.assertEqual(release.asset_url, LEGACY_URL)
        self.assertEqual(release.asset_digest, hashlib.sha256(payload).hexdigest())
        for kwargs in ({"release_url": media_common.LATEST_RELEASE_URL}, {"asset_name": media_builder.ASSET_NAME}):
            with self.assertRaises(self.legacy.UpdateError):
                self.legacy.fetch_latest_release(**kwargs)

    def test_hardened_legacy_rejects_media_only_asset_without_channel_fallback(self):
        metadata = self.metadata(b"candidate", asset=media_builder.ASSET_NAME)
        with mock.patch.object(self.legacy, "_urlopen", return_value=Response(json.dumps(metadata).encode(), LEGACY_LATEST)):
            release = self.legacy.fetch_latest_release()
        self.assertIsNone(release.asset_url)
        with self.assertRaises(self.legacy.UpdateError):
            self.legacy.download_release_asset(release)

    def test_hardened_legacy_rejects_other_repository_asset_http_and_tag_urls(self):
        bad = (LEGACY_URL.replace("cloudrelay-imagegen-skill/", "cloudrelay-mediagen-skill/"),
               LEGACY_URL.replace("cloudrelay-imagegen.skill", "cloudrelay-mediagen.skill"),
               LEGACY_URL.replace("https://", "http://"), LEGACY_URL.replace("/v1.5.1/", "/v1.5.2/"))
        for url in bad:
            with self.subTest(url=url), self.assertRaises(self.legacy.UpdateError):
                self.legacy.download_release_asset(self.release(b"candidate", url=url))

    def test_hardened_legacy_rejects_non_15_release_and_archive_versions(self):
        for version in ("2.0.1", "1.6.0", "1.4.9"):
            with self.subTest(version=version):
                metadata = self.metadata(b"candidate", version=version)
                with mock.patch.object(self.legacy, "_urlopen", return_value=Response(json.dumps(metadata).encode(), LEGACY_LATEST)):
                    with self.assertRaises(self.legacy.UpdateError):
                        self.legacy.fetch_latest_release()
                payload = self.rewrite_archive({"VERSION": (version + "\n").encode()})
                with self.assertRaises(self.legacy.UpdateError):
                    self.legacy.stage_release(payload, self.root)

    def test_hardened_legacy_rejects_media_archive_and_media_rejects_legacy(self):
        with self.assertRaises(self.legacy.UpdateError):
            self.legacy.stage_release(self.package(legacy=False), self.root)
        with self.assertRaises(media_common.UpdateError):
            media_common.stage_release(self.package(), self.root)

    def test_hardened_legacy_rejects_media_target_before_modification(self):
        target = self.tree(legacy=False)
        self.preserve(target)
        before = snapshot(target)
        with self.assertRaises(self.legacy.UpdateError):
            self.legacy.apply_staged_release(self.tree(name="disposable stage"), target)
        self.assertEqual(snapshot(target), before)
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            result = self.legacy_updater.main(["--target-dir", str(target), "--auto"])
        self.assertEqual(result, 1)
        self.assertEqual(snapshot(target), before)

    def test_hardened_legacy_archive_missing_traversal_and_symlink_rejected(self):
        variants = [self.rewrite_archive(omit=("scripts/generate_image.py",)),
                    self.rewrite_archive(extras=(("../escape", b"bad"),))]
        stream = BytesIO(self.package())
        with zipfile.ZipFile(stream, "a") as archive:
            link = zipfile.ZipInfo("unmanaged-link")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(link, "../outside")
        variants.append(stream.getvalue())
        for index, payload in enumerate(variants):
            with self.subTest(case=index), self.assertRaises(self.legacy.UpdateError):
                self.legacy.stage_release(payload, self.root)

    def test_hardened_legacy_tag_archive_mismatch_preserves_target(self):
        target = self.root / "legacy target"
        self.helpers._materialize_fixture(target)
        self.preserve(target)
        before = snapshot(target)
        payload = self.package()
        version = "1.5.2"
        url = LEGACY_URL.replace("/v1.5.1/", "/v1.5.2/")
        metadata = self.metadata(payload, version=version, url=url)
        with mock.patch.object(self.legacy, "_urlopen", side_effect=[Response(json.dumps(metadata).encode(), LEGACY_LATEST), Response(payload, url)]), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            status = self.legacy_updater.main(["--target-dir", str(target), "--auto"])
        self.assertEqual(status, 1)
        self.assertEqual(snapshot(target), before)

    def test_legacy_builder_rejects_broken_runtime_python(self):
        source = self.tree()
        (source / "scripts/generate_image.py").write_text("def broken(\n", encoding="utf-8")
        with self.assertRaises(legacy_builder.PackageError):
            legacy_builder.build_package(source, self.root / "dist")
        self.assertFalse((self.root / "dist").exists())

    def test_legacy_builder_rejects_ambiguous_skill_names(self):
        for index, header in enumerate((
            "---\nname: cloudrelay-imagegen\n'name': cloudrelay-mediagen\n---\n",
            "---\nname: cloudrelay-imagegen\n  name: cloudrelay-mediagen\n---\n",
            "---\nname: \"cloudrelay-imagegen\n---\n",
        )):
            with self.subTest(case=index):
                source = self.tree(name=f"ambiguous-{index}")
                (source / "SKILL.md").write_text(header, encoding="utf-8")
                with self.assertRaises(legacy_builder.PackageError):
                    legacy_builder.build_package(source, self.root / f"ambiguous-dist-{index}")

    def test_hardened_legacy_preserves_valid_build_metadata_normalization(self):
        version = self.legacy.parse_version("v1.5.2+build.1")
        self.assertEqual(version.normalized, "1.5.2+build.1")
        self.assertEqual(self.legacy.compare_versions(version, self.legacy.parse_version("1.5.2+build.2")), 0)
        for malformed in ("1.5.2+.", "1.5.2+build..1"):
            with self.subTest(version=malformed), self.assertRaises(self.legacy.UpdateError):
                self.legacy.parse_version(malformed)

    def test_hardened_legacy_rejects_distinct_tag_archive_build_metadata(self):
        target = self.root / "legacy target"
        self.helpers._materialize_fixture(target)
        self.preserve(target)
        before = snapshot(target)
        payload = self.rewrite_archive({"VERSION": b"1.5.2+build.2\n"})
        version = "1.5.2+build.1"
        url = LEGACY_URL.replace("/v1.5.1/", "/v1.5.2%2Bbuild.1/")
        metadata = self.metadata(payload, version=version, url=url)
        with mock.patch.object(self.legacy, "_urlopen", side_effect=[Response(json.dumps(metadata).encode(), LEGACY_LATEST), Response(payload, url)]), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            status = self.legacy_updater.main(["--target-dir", str(target), "--auto"])
        self.assertEqual(status, 1)
        self.assertEqual(snapshot(target), before)

    def test_legacy_builder_default_is_independent_of_current_directory(self):
        output = self.root / "cli-output"
        with mock.patch.object(legacy_builder, "build_package", wraps=legacy_builder.build_package) as build, redirect_stdout(StringIO()):
            result = legacy_builder.main(["--output-dir", str(output)])
        self.assertEqual(result, 0)
        self.assertEqual(build.call_args.args, (LEGACY, output))
        self.assertTrue((output / legacy_builder.ASSET_NAME).is_file())
        self.assertFalse((output / media_builder.ASSET_NAME).exists())


if __name__ == "__main__":
    unittest.main()
