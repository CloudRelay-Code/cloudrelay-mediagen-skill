from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
import zipfile
from unittest import mock

from scripts import _update_common as update_common


REPOSITORY = Path(__file__).resolve().parent.parent
CURRENT_VERSION = (REPOSITORY / "VERSION").read_text(encoding="utf-8").strip()


class UpdateTests(unittest.TestCase):
    class _Response:
        def __init__(self, payload: bytes) -> None:
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, *_args):
            payload, self.payload = self.payload, b""
            return payload

    def test_release_metadata_parses_tag_asset_and_digest(self) -> None:
        payload = {
            "tag_name": f"v{CURRENT_VERSION}",
            "published_at": "2026-08-31T00:00:00Z",
            "assets": [
                {
                    "name": "cloudrelay-mediagen.skill",
                    "browser_download_url": f"https://github.com/CloudRelay-Code/cloudrelay-mediagen-skill/releases/download/v{CURRENT_VERSION}/cloudrelay-mediagen.skill",
                    "digest": "sha256:" + "a" * 64,
                }
            ],
        }
        with mock.patch(
            "scripts._update_common._urlopen",
            return_value=self._Response(json.dumps(payload).encode("utf-8")),
        ):
            release = update_common.fetch_latest_release(timeout=1)
        self.assertEqual(release.version.normalized, CURRENT_VERSION)
        self.assertEqual(release.asset_digest, "a" * 64)

    def test_release_asset_digest_mismatch_is_rejected(self) -> None:
        release = update_common.ReleaseInfo(
            version=update_common.parse_version(CURRENT_VERSION),
            tag_name=f"v{CURRENT_VERSION}",
            asset_url=f"https://github.com/CloudRelay-Code/cloudrelay-mediagen-skill/releases/download/v{CURRENT_VERSION}/cloudrelay-mediagen.skill",
            asset_digest="0" * 64,
            published_at=None,
        )
        with mock.patch(
            "scripts._update_common._urlopen",
            return_value=self._Response(b"not-the-release"),
        ):
            with self.assertRaises(update_common.UpdateError):
                update_common.download_release_asset(release, timeout=1)

    def test_semantic_version_comparison_handles_prerelease(self) -> None:
        self.assertLess(
            update_common.compare_versions(
                update_common.parse_version("1.2.0-rc.1"),
                update_common.parse_version("1.2.0"),
            ),
            0,
        )
        self.assertGreater(
            update_common.compare_versions(
                update_common.parse_version("v1.2.1"),
                update_common.parse_version("1.2.0"),
            ),
            0,
        )
        with self.assertRaises(update_common.UpdateError):
            update_common.parse_version("1.2.0-01")

    def _release_archive(self, prefix: str | None = None) -> bytes:
        prefix = prefix or f"cloudrelay-mediagen-skill-{CURRENT_VERSION}"
        stream = BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for relative in update_common.REQUIRED_FILES:
                archive.write(
                    REPOSITORY / relative,
                    f"{prefix}/{relative.as_posix()}",
                )
        return stream.getvalue()

    def test_release_archive_is_validated_and_extracted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "stage"
            update_common.extract_release_archive(self._release_archive(), destination)
            self.assertEqual(
                update_common.validate_skill_tree(destination).normalized,
                CURRENT_VERSION,
            )
            self.assertTrue((destination / "scripts/update.py").is_file())

    def test_archive_path_traversal_is_rejected_before_writing(self) -> None:
        stream = BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("../outside.txt", "must not escape")
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(update_common.UpdateError):
                update_common.extract_release_archive(stream.getvalue(), Path(temporary) / "stage")
            self.assertFalse((Path(temporary).parent / "outside.txt").exists())

    def test_apply_preserves_unknown_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "installed"
            target.mkdir()
            for relative in update_common.REQUIRED_FILES:
                source = REPOSITORY / relative
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            custom = target / "user-not-managed.txt"
            custom.write_text("keep me", encoding="utf-8")

            stage, _ = update_common.stage_release(self._release_archive(), target.parent)
            update_common.apply_staged_release(stage, target)

            self.assertEqual(custom.read_text(encoding="utf-8"), "keep me")
            self.assertEqual(
                (target / "VERSION").read_text(encoding="utf-8").strip(),
                CURRENT_VERSION,
            )

    def test_apply_rechecks_version_under_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "installed"
            target.mkdir()
            for relative in update_common.REQUIRED_FILES:
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(REPOSITORY / relative, destination)
            newer_version = f"{int(CURRENT_VERSION.split('.')[0]) + 1}.0.0"
            (target / "VERSION").write_text(newer_version + "\n", encoding="utf-8")

            stage, _ = update_common.stage_release(self._release_archive(), target.parent)
            with self.assertRaises(update_common.UpdateError):
                update_common.apply_staged_release(stage, target)
            self.assertEqual((target / "VERSION").read_text(encoding="utf-8").strip(), newer_version)

    def test_stale_update_lock_is_reclaimed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "installed"
            target.mkdir()
            lock = target.parent / ".installed.update.lock"
            lock.mkdir()
            (lock / "owner").write_text("2147483647", encoding="ascii")
            with update_common._update_lock(target):
                self.assertTrue(lock.is_dir())
            self.assertFalse(lock.exists())

    def test_live_update_lock_is_not_reclaimed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "installed"
            target.mkdir()
            lock = target.parent / ".installed.update.lock"
            lock.mkdir()
            (lock / "owner").write_text(str(os.getpid()), encoding="ascii")
            with self.assertRaises(update_common.UpdateError):
                with update_common._update_lock(target):
                    pass

    @unittest.skipUnless(os.name != "nt", "FIFO special files are POSIX-only")
    def test_apply_rejects_special_destination_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "installed"
            target.mkdir()
            for relative in update_common.REQUIRED_FILES:
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(REPOSITORY / relative, destination)
            fifo = target / "scripts/update.py"
            fifo.unlink()
            os.mkfifo(fifo)

            stage, _ = update_common.stage_release(self._release_archive(), target.parent)
            with self.assertRaises(update_common.UpdateError):
                update_common.apply_staged_release(stage, target)

    def test_apply_rejects_symlinked_managed_directory(self) -> None:
        repository = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "installed"
            target.mkdir()
            for relative in update_common.REQUIRED_FILES:
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(repository / relative, destination)
            outside = root / "outside"
            outside.mkdir()
            outside_file = outside / "configure_api_key.py"
            outside_file.write_text("do not overwrite", encoding="utf-8")
            shutil.rmtree(target / "scripts")
            try:
                os.symlink(outside, target / "scripts", target_is_directory=True)
            except (NotImplementedError, OSError) as error:
                self.skipTest(f"symlink creation is unavailable: {error}")

            stage, _ = update_common.stage_release(self._release_archive(), target.parent)
            with self.assertRaises(update_common.UpdateError):
                update_common.apply_staged_release(stage, target)
            self.assertEqual(outside_file.read_text(encoding="utf-8"), "do not overwrite")

    def test_safe_path_rejects_symlinked_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            real_target = root / "real"
            real_target.mkdir()
            linked_target = root / "linked"
            try:
                os.symlink(real_target, linked_target, target_is_directory=True)
            except (NotImplementedError, OSError) as error:
                self.skipTest(f"symlink creation is unavailable: {error}")
            with self.assertRaises(update_common.UpdateError):
                update_common.ensure_safe_path(linked_target)


if __name__ == "__main__":
    unittest.main()
