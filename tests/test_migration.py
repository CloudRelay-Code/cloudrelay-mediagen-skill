from __future__ import annotations

from collections.abc import Mapping
from contextlib import redirect_stderr, redirect_stdout
import builtins
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from scripts import _update_common as common
from scripts import configure_api_key as credentials
from scripts import migrate_from_imagegen as migration


class NamesOnlyEnvironment(Mapping):
    """Any credential variable value access makes the test fail."""

    def __init__(self, names=()):
        self.names = tuple(names)

    def __iter__(self):
        return iter(self.names)

    def __len__(self):
        return len(self.names)

    def __getitem__(self, name):
        if name in credentials.PROVIDER_ENV_NAMES.values():
            raise AssertionError(f"Credential environment value read: {name}")
        raise KeyError(name)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="media migration " )
        self.root = Path(self.temporary.name)
        self.source = self.root / "legacy target"
        self.target = self.root / "media target"
        self.config = self.root / "external config" / "cloudrelay"
        self._skill(self.source, migration.LEGACY_SKILL_NAME)
        self._skill(self.target, migration.SKILL_NAME)
        self.config.mkdir(parents=True)
        self.subprocess_environment = {
            name: os.environ[name] for name in ("SystemRoot", "WINDIR") if name in os.environ
        }
        self.environment = mock.patch.object(migration.os, "environ", NamesOnlyEnvironment())
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def _skill(self, path, identity):
        path.mkdir(parents=True, exist_ok=True)
        (path / "SKILL.md").write_text(
            f"---\nname: {identity}\ndescription: test\n---\n# Fixture\n", encoding="utf-8"
        )
        (path / "VERSION").write_text("1.5.0\n" if identity == migration.LEGACY_SKILL_NAME else "2.0.0\n", encoding="utf-8")

    def _run(self, **kwargs):
        return migration.migrate(self.source, self.target, config_dir=self.config, **kwargs)

    def _snapshot(self, root):
        return {
            str(path.relative_to(root)): None if path.is_dir() else path.read_bytes()
            for path in root.rglob("*")
        }

    def _user_files(self):
        for directory in (self.source, self.target):
            (directory / "custom user notes.txt").write_text("Keep my notes exactly.\n", encoding="utf-8")
            for name in migration.OUTPUT_DIRECTORY_NAMES:
                generated = directory / name
                generated.mkdir()
                (generated / "user-result.bin").write_bytes(b"existing generated output\x00\xff")
            for filename in credentials._SECRET_FILE_NAMES.values():
                (directory / filename).write_text("secret-test-sentinel-do-not-read", encoding="utf-8")

    def test_default_mode_writes_nothing(self):
        self._user_files()
        before = self._snapshot(self.root)
        report = self._run()
        self.assertEqual(report.action, "dry-run")
        self.assertEqual(before, self._snapshot(self.root))

    def test_dry_run_writes_nothing(self):
        before = self._snapshot(self.root)
        self._run(dry_run=True)
        self.assertEqual(before, self._snapshot(self.root))

    def test_dry_run_wins_over_yes(self):
        before = self._snapshot(self.root)
        self.assertEqual(self._run(yes=True, dry_run=True).action, "dry-run")
        self.assertEqual(before, self._snapshot(self.root))

    def test_yes_creates_only_receipt_and_preserves_all_data(self):
        self._user_files()
        before_source = self._snapshot(self.source)
        before_target = self._snapshot(self.target)
        report = self._run(yes=True)
        self.assertEqual(report.action, "metadata-handoff-recorded")
        self.assertEqual(before_source, self._snapshot(self.source))
        after_target = self._snapshot(self.target)
        receipt_bytes = after_target.pop(migration.RECEIPT_NAME)
        self.assertEqual(before_target, after_target)
        receipt = json.loads(receipt_bytes)
        self.assertEqual(receipt["source_identity"], migration.LEGACY_SKILL_NAME)
        self.assertEqual(receipt["target_identity"], migration.SKILL_NAME)
        self.assertEqual(receipt["source_dir"], str(self.source))
        self.assertNotIn("secret-test-sentinel", receipt_bytes.decode())
        self.assertTrue(all(report.output_directories.values()))

    def test_existing_receipt_is_never_overwritten(self):
        self._run(yes=True)
        receipt = self.target / migration.RECEIPT_NAME
        original = receipt.read_bytes()
        with self.assertRaisesRegex(migration.MigrationError, "left unchanged"):
            self._run(yes=True)
        self.assertEqual(original, receipt.read_bytes())

    def test_conflicting_user_file_is_never_overwritten_or_read(self):
        receipt = self.target / migration.RECEIPT_NAME
        receipt.write_bytes(b"This is a user-owned file, not a receipt.")
        with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("Unexpected read")):
            with self.assertRaisesRegex(migration.MigrationError, "left unchanged"):
                self._run(yes=True)
        self.assertEqual(receipt.read_bytes(), b"This is a user-owned file, not a receipt.")

    def test_conflicting_directory_is_never_removed(self):
        receipt = self.target / migration.RECEIPT_NAME
        receipt.mkdir()
        with self.assertRaises(migration.MigrationError):
            self._run(yes=True)
        self.assertTrue(receipt.is_dir())

    def test_missing_target_is_report_only_and_never_phantom_install(self):
        absent = self.root / "new location" / migration.SKILL_NAME
        for kwargs in ({}, {"dry_run": True}, {"yes": True}):
            with self.subTest(kwargs=kwargs):
                report = migration.migrate(self.source, absent, config_dir=self.config, **kwargs)
                self.assertFalse(report.target_installed)
                self.assertFalse(absent.parent.exists())
                text = migration.format_report(report)
                self.assertIn(migration.INSTALL_COMMAND, text)
                self.assertIn("Install the new skill separately", text)
        self.assertEqual(report.action, "installation-required")

    def test_missing_target_requires_exact_intended_media_basename(self):
        for name in ("cloudrelay-imagegen", "other-skill", "CLOUDRELAY-MEDIAGEN"):
            with self.subTest(name=name), self.assertRaises(migration.MigrationError):
                migration.migrate(self.source, self.root / name, config_dir=self.config)

    def test_source_identity_must_be_legacy(self):
        self._skill(self.source, migration.SKILL_NAME)
        with self.assertRaises(migration.MigrationError):
            self._run(yes=True)
        self.assertFalse((self.target / migration.RECEIPT_NAME).exists())

    def test_target_identity_must_be_media(self):
        self._skill(self.target, migration.LEGACY_SKILL_NAME)
        before = self._snapshot(self.root)
        with self.assertRaises(migration.MigrationError):
            self._run(yes=True)
        self.assertEqual(before, self._snapshot(self.root))

    def test_duplicate_identity_fields_are_rejected(self):
        for directory, identity in ((self.source, migration.LEGACY_SKILL_NAME), (self.target, migration.SKILL_NAME)):
            with self.subTest(directory=directory):
                skill = directory / "SKILL.md"
                original = skill.read_text(encoding="utf-8")
                skill.write_text(f"---\nname: {identity}\nname: {identity}\n---\n", encoding="utf-8")
                try:
                    with self.assertRaises(migration.MigrationError):
                        self._run()
                finally:
                    skill.write_text(original, encoding="utf-8")

    def test_missing_source_or_identity_is_rejected(self):
        with self.assertRaises(migration.MigrationError):
            migration.migrate(self.root / "missing", self.target, config_dir=self.config)
        (self.source / "SKILL.md").unlink()
        with self.assertRaises(migration.MigrationError):
            self._run()

    def test_existing_empty_target_is_not_given_a_skill_identity(self):
        target = self.root / "empty target"
        target.mkdir()
        with self.assertRaises(migration.MigrationError):
            migration.migrate(self.source, target, yes=True, config_dir=self.config)
        self.assertEqual(list(target.iterdir()), [])

    def test_equal_and_nested_paths_are_rejected(self):
        for target in (self.source, self.source / migration.SKILL_NAME, self.source.parent):
            with self.subTest(target=target), self.assertRaises(migration.MigrationError):
                migration.migrate(self.source, target, config_dir=self.config)

    def test_filesystem_roots_are_rejected(self):
        anchor = Path(self.root.anchor)
        for source, target in ((anchor, self.target), (self.source, anchor)):
            with self.subTest(source=source, target=target), self.assertRaises(migration.MigrationError):
                migration.migrate(source, target, config_dir=self.config)

    def test_lexical_traversal_and_unsafe_components_are_rejected(self):
        candidates = [
            str(self.root / "child" / ".." / migration.SKILL_NAME),
            str(self.root / "NUL" / migration.SKILL_NAME),
            str(self.root / "CON.txt" / migration.SKILL_NAME),
            str(self.root / "trailing." / migration.SKILL_NAME),
            str(self.root / "trailing " / migration.SKILL_NAME),
            str(self.root / "file:stream" / migration.SKILL_NAME),
            str(self.root / "bad?component" / migration.SKILL_NAME),
            "\\\\server\\share\\cloudrelay-mediagen",
            "\x00",
        ]
        for target in candidates:
            with self.subTest(target=target), self.assertRaises(migration.MigrationError):
                migration.migrate(self.source, target, config_dir=self.config)

    def test_native_paths_with_spaces_are_supported(self):
        report = self._run()
        self.assertEqual(report.source_dir, self.source)
        self.assertEqual(report.target_dir, self.target)

    def test_forward_slash_paths_are_supported_on_this_host(self):
        report = migration.migrate(self.source.as_posix(), self.target.as_posix(), config_dir=self.config)
        self.assertEqual(report.source_dir, self.source)
        self.assertEqual(report.target_dir, self.target)

    @unittest.skipUnless(os.name == "nt", "Requires a native Windows host")
    def test_native_windows_backslash_paths(self):
        report = migration.migrate(str(self.source), str(self.target), config_dir=self.config)
        self.assertIn("\\", str(report.source_dir))

    @unittest.skipUnless(os.name == "posix", "Requires a native POSIX host; forward slashes on Windows are separate")
    def test_native_posix_paths(self):
        report = self._run()
        self.assertTrue(str(report.source_dir).startswith("/"))

    def test_credential_presence_never_reads_file_env_or_registry_values(self):
        secret = "secret-test-sentinel-do-not-read"
        for filename in credentials._SECRET_FILE_NAMES.values():
            (self.config / filename).write_text(secret, encoding="utf-8")
        key_names = set(credentials._SECRET_FILE_NAMES.values())
        original_open = io.open
        original_builtin_open = builtins.open
        original_os_open = os.open
        def guarded_open(file, *args, **kwargs):
            if not isinstance(file, int) and Path(file).name in key_names:
                raise AssertionError("Credential file contents must never be opened")
            return original_open(file, *args, **kwargs)
        def guarded_builtin_open(file, *args, **kwargs):
            if not isinstance(file, int) and Path(file).name in key_names:
                raise AssertionError("Credential file contents must never be opened")
            return original_builtin_open(file, *args, **kwargs)
        def guarded_os_open(file, *args, **kwargs):
            if Path(file).name in key_names:
                raise AssertionError("Credential file descriptors must never be opened")
            return original_os_open(file, *args, **kwargs)
        with (
            mock.patch.object(io, "open", side_effect=guarded_open),
            mock.patch.object(builtins, "open", side_effect=guarded_builtin_open),
            mock.patch.object(os, "open", side_effect=guarded_os_open),
            mock.patch.object(credentials, "_is_configured", side_effect=AssertionError("Value-reading helper used")),
            mock.patch.object(credentials, "_read_windows_user_variable", side_effect=AssertionError("Registry values queried")),
            mock.patch.object(credentials, "_secret_file", side_effect=AssertionError("Value-aware helper used")),
        ):
            report = self._run(yes=True)
        self.assertTrue(all(report.credential_presence.values()))
        self.assertNotIn(secret, migration.format_report(report))
        self.assertNotIn(secret, (self.target / migration.RECEIPT_NAME).read_text(encoding="utf-8"))

    def test_process_environment_detection_uses_names_only(self):
        environment = NamesOnlyEnvironment([credentials.PROVIDER_ENV_NAMES["gpt"]])
        with mock.patch.object(migration.os, "environ", environment):
            report = self._run()
        self.assertEqual(report.credential_presence, {"gpt": True, "gemini": False, "grok": False})

    def test_external_key_store_is_shared_and_unchanged(self):
        key = self.config / credentials._SECRET_FILE_NAMES["grok"]
        key.write_bytes(b"shared-external-key-sentinel")
        original = key.read_bytes()
        report = self._run(yes=True)
        self.assertTrue(report.credential_presence["grok"])
        self.assertEqual(key.read_bytes(), original)
        self.assertFalse((self.target / key.name).exists())
        self.assertFalse((self.source / key.name).exists())

    def test_empty_key_file_is_presence_not_a_validity_claim(self):
        (self.source / credentials._SECRET_FILE_NAMES["gemini"]).touch()
        report = self._run()
        self.assertTrue(report.credential_presence["gemini"])
        self.assertIn("presence only", migration.format_report(report))
        self.assertIn("values are never read or validated", migration.format_report(report))

    def test_report_includes_all_agent_reload_hints_and_original_output_paths(self):
        report = self._run()
        text = migration.format_report(report)
        for agent, hint in migration.RELOAD_HINTS.items():
            self.assertIn(f"{agent}: {hint}", text)
        self.assertIn("/skills reload", text)
        self.assertIn(str(self.source / "generated-images"), text)
        self.assertIn(str(self.target / "generated-videos"), text)
        self.assertIn("Legacy skill detected: yes", text)
        self.assertIn("metadata handoff only", text)
        self.assertIn("Registry-only settings are not inspected", text)

    def _assert_subprocess_read_only(self, flags):
        self._user_files()
        runtime = self.root / "clean copied runtime"
        scripts = runtime / "scripts"
        scripts.mkdir(parents=True)
        original_scripts = Path(migration.__file__).parent
        for filename in ("migrate_from_imagegen.py", "_update_common.py", "configure_api_key.py"):
            (scripts / filename).write_bytes((original_scripts / filename).read_bytes())
        (runtime / "VERSION").write_text("2.0.0", encoding="utf-8")
        child_environment = {
            **self.subprocess_environment,
            "PYTHONUTF8": "1",
            "HOME": str(self.root / "isolated home"),
            "USERPROFILE": str(self.root / "isolated home"),
            "XDG_CONFIG_HOME": str(self.config.parent),
            "TEMP": str(self.root),
            "TMP": str(self.root),
        }
        self.assertNotIn("PYTHONDONTWRITEBYTECODE", child_environment)
        self.assertNotIn("PYTHONPYCACHEPREFIX", child_environment)
        before = self._snapshot(self.root)
        command = [
            sys.executable, str(scripts / "migrate_from_imagegen.py"),
            "--source-dir", str(self.source), "--target-dir", str(self.target), *flags,
        ]
        self.assertNotIn("-B", command)
        result = subprocess.run(
            command, cwd=runtime, env=child_environment,
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("No files were written", result.stdout)
        after = self._snapshot(self.root)
        self.assertEqual(set(before), set(after), "Ordinary CLI execution created files")
        self.assertEqual(before, after)

    def test_subprocess_default_creates_no_bytecode_or_other_files(self):
        self._assert_subprocess_read_only([])

    def test_subprocess_dry_run_creates_no_bytecode_or_other_files(self):
        self._assert_subprocess_read_only(["--dry-run"])

    def test_cli_default_and_dry_run_are_nonmutating(self):
        before = self._snapshot(self.root)
        for flags in ([], ["--dry-run"], ["--yes", "--dry-run"]):
            stdout = io.StringIO()
            with redirect_stdout(stdout), mock.patch.object(migration, "_default_config_dir", return_value=self.config):
                result = migration.main(["--source-dir", str(self.source), "--target-dir", str(self.target), *flags])
            self.assertEqual(result, 0)
            self.assertIn("No files were written", stdout.getvalue())
            self.assertEqual(before, self._snapshot(self.root))

    def test_cli_yes_requires_separate_install_for_absent_target(self):
        absent = self.root / migration.SKILL_NAME
        stdout = io.StringIO()
        with redirect_stdout(stdout), mock.patch.object(migration, "_default_config_dir", return_value=self.config):
            result = migration.main(["--source-dir", str(self.source), "--target-dir", str(absent), "--yes"])
        self.assertEqual(result, 1)
        self.assertIn(migration.INSTALL_COMMAND, stdout.getvalue())
        self.assertFalse(absent.exists())

    def test_cli_reports_identity_refusal(self):
        self._skill(self.source, "unrelated-skill")
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            result = migration.main(["--source-dir", str(self.source), "--target-dir", str(self.target), "--yes"])
        self.assertEqual(result, 1)
        self.assertIn("Migration refused", stderr.getvalue())

    def _symlink(self, link, target, directory=False):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except OSError as error:
            self.skipTest(f"Host cannot create a test symlink: {error}")

    def test_symlink_source_is_rejected(self):
        link = self.root / "linked legacy"
        self._symlink(link, self.source, directory=True)
        with self.assertRaises(migration.MigrationError):
            migration.migrate(link, self.target, yes=True, config_dir=self.config)

    def test_symlink_target_parent_is_rejected(self):
        real = self.root / "real parent"
        real.mkdir()
        link = self.root / "linked parent"
        self._symlink(link, real, directory=True)
        with self.assertRaises(migration.MigrationError):
            migration.migrate(self.source, link / migration.SKILL_NAME, yes=True, config_dir=self.config)
        self.assertEqual(list(real.iterdir()), [])

    def test_symlink_skill_identity_is_rejected_before_reading(self):
        skill = self.source / "SKILL.md"
        original = self.root / "original identity.md"
        skill.rename(original)
        self._symlink(skill, original)
        with self.assertRaises(migration.MigrationError):
            self._run()

    def test_symlink_receipt_is_rejected_and_external_file_preserved(self):
        external = self.root / "external.txt"
        external.write_bytes(b"user-owned external file")
        self._symlink(self.target / migration.RECEIPT_NAME, external)
        with self.assertRaises(migration.MigrationError):
            self._run(yes=True)
        self.assertEqual(external.read_bytes(), b"user-owned external file")

    @unittest.skipUnless(os.name == "nt", "A real junction requires Windows")
    def test_real_windows_junction_is_rejected(self):
        junction = self.root / "junction target"
        # mklink creates only this temporary junction; cleanup removes the link itself.
        # Restore the actual environment for launching the Windows command interpreter.
        self.environment.stop()
        try:
            result = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(self.target)], capture_output=True, text=True)
        finally:
            self.environment.start()
        if result.returncode:
            self.skipTest(f"Host cannot create a test junction: {result.stderr.strip()}")
        try:
            with self.assertRaises(migration.MigrationError):
                migration.migrate(self.source, junction, yes=True, config_dir=self.config)
            self.assertFalse((self.target / migration.RECEIPT_NAME).exists())
        finally:
            junction.rmdir()

    def test_windows_reparse_fallback_without_path_is_junction(self):
        path_type = type(self.source)
        original_lstat = path_type.lstat
        source = self.source
        class ReparseMetadata:
            def __init__(self, original):
                self.original = original
                self.st_file_attributes = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            def __getattr__(self, name):
                return getattr(self.original, name)
        def fake_lstat(path, *args, **kwargs):
            metadata = original_lstat(path, *args, **kwargs)
            return ReparseMetadata(metadata) if path == source else metadata
        with (
            mock.patch.object(path_type, "lstat", fake_lstat),
            mock.patch.object(path_type, "is_junction", None, create=True),
            mock.patch.object(common.os, "name", "nt"),
            mock.patch.object(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400, create=True),
        ):
            # Concrete Path is constructed before mocking the platform.
            with self.assertRaises(common.UpdateError):
                common.ensure_safe_path(source)


if __name__ == "__main__":
    unittest.main()
