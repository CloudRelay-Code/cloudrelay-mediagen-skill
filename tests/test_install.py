from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import install


class InstallTests(unittest.TestCase):
    def _assert_destination_suffix(self, destination: Path, relative_root: str) -> None:
        expected = tuple(Path(relative_root).parts) + (install.SKILL_NAME,)
        actual = destination.parts[-len(expected) :]
        self.assertEqual(
            tuple(part.casefold() for part in actual),
            tuple(part.casefold() for part in expected),
        )

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        for relative_path in install.CORE_FILES + install.CODEX_FILES:
            path = self.source / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(relative_path), encoding="utf-8")
        self.home = self.root / "home"
        self.project = self.root / "project"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_user_destinations_match_client_conventions(self) -> None:
        expected = {
            "codex": ".codex/skills",
            "claude-code": ".claude/skills",
            "gemini-cli": ".gemini/skills",
            "openclaw": ".openclaw/skills",
            "cursor": ".cursor/skills",
        }
        for client, relative_root in expected.items():
            with self.subTest(client=client):
                destination = install.destination_for(
                    client,
                    "user",
                    home=self.home,
                    project_dir=self.project,
                )
                self._assert_destination_suffix(destination, relative_root)

    def test_project_destinations_match_client_conventions(self) -> None:
        expected = {
            "codex": ".agents/skills",
            "claude-code": ".claude/skills",
            "gemini-cli": ".gemini/skills",
            "openclaw": "skills",
            "cursor": ".cursor/skills",
        }
        for client, relative_root in expected.items():
            with self.subTest(client=client):
                destination = install.destination_for(
                    client,
                    "project",
                    home=self.home,
                    project_dir=self.project,
                )
                self._assert_destination_suffix(destination, relative_root)

    def test_install_copies_only_runtime_files(self) -> None:
        for client in install.CLIENTS:
            with self.subTest(client=client):
                destination = install.install_client(
                    client,
                    "user",
                    source_root=self.source,
                    home=self.home / client,
                    project_dir=self.project,
                )
                for relative_path in install.CORE_FILES:
                    self.assertTrue((destination / relative_path).is_file())
                self.assertEqual(
                    (destination / "agents/openai.yaml").is_file(),
                    client == "codex",
                )

    def test_existing_destination_requires_force(self) -> None:
        kwargs = {
            "source_root": self.source,
            "home": self.home,
            "project_dir": self.project,
        }
        install.install_client("cursor", "user", **kwargs)
        with self.assertRaises(install.InstallError):
            install.install_client("cursor", "user", **kwargs)
        destination = install.install_client("cursor", "user", force=True, **kwargs)
        self.assertTrue((destination / "SKILL.md").is_file())

    def test_dry_run_does_not_create_destination(self) -> None:
        destination = install.install_client(
            "openclaw",
            "project",
            source_root=self.source,
            home=self.home,
            project_dir=self.project,
            dry_run=True,
        )
        self.assertFalse(destination.exists())

    def test_dry_run_allows_an_existing_destination(self) -> None:
        destination = install.destination_for(
            "codex",
            "user",
            home=self.home,
            project_dir=self.project,
        )
        destination.mkdir(parents=True)
        result = install.install_client(
            "codex",
            "user",
            source_root=self.source,
            home=self.home,
            project_dir=self.project,
            dry_run=True,
        )
        self.assertEqual(result, destination)


if __name__ == "__main__":
    unittest.main()
