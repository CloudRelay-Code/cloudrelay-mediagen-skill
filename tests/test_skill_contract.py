from __future__ import annotations

from pathlib import Path
import unittest

import yaml


class SkillContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repository = Path(__file__).resolve().parent.parent
        text = (cls.repository / "SKILL.md").read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            raise AssertionError("SKILL.md must start with YAML frontmatter")
        _, frontmatter, cls.body = text.split("---", 2)
        cls.metadata = yaml.safe_load(frontmatter)

    def test_frontmatter_uses_portable_fields_only(self) -> None:
        self.assertEqual(set(self.metadata), {"name", "description"})
        self.assertEqual(self.metadata["name"], "cloudrelay-imagegen")
        self.assertIsInstance(self.metadata["description"], str)
        self.assertIn("CloudRelay", self.metadata["description"])

    def test_runtime_files_exist(self) -> None:
        for relative_path in (
            "scripts/configure_api_key.py",
            "scripts/generate_image.py",
            "agents/openai.yaml",
        ):
            with self.subTest(path=relative_path):
                self.assertTrue((self.repository / relative_path).is_file())

    def test_unified_channels_and_credentials_are_documented(self) -> None:
        for provider, environment, group in (
            ("GPT", "CLOUDRELAY_GPT_IMAGE_API_KEY", "生图专用"),
            ("Gemini", "CLOUDRELAY_GEMINI_API_KEY", "gemini"),
            ("Grok", "CLOUDRELAY_GROK_API_KEY", "grok"),
        ):
            with self.subTest(provider=provider):
                self.assertIn(provider, self.body)
                self.assertIn(environment, self.body)
                self.assertIn(group, self.body)
        self.assertIn("GET  /v1/media/models", self.body)
        self.assertIn("POST /v1/images/generations/async", self.body)
        self.assertIn("GET  /v1/images/tasks/{task_id}", self.body)
        self.assertIn("gpt-image-2.5-flare", self.body)
        self.assertIn("grok-imagine-image-2.0", self.body)
        self.assertIn("async=false", self.body)
        self.assertIn("Authorization: Bearer", self.body)

    def test_gemini_confirmation_and_parameter_report_are_documented(self) -> None:
        self.assertIn("gemini-3.1-flash-image-preview", self.body)
        self.assertIn("gemini-3-pro-image-preview", self.body)
        self.assertIn("Do not choose between them silently", self.body)
        self.assertIn("effective parameter summary", self.body)
        self.assertIn("credential group", self.body)

    def test_openai_metadata_is_valid(self) -> None:
        metadata = yaml.safe_load(
            (self.repository / "agents/openai.yaml").read_text(encoding="utf-8")
        )
        prompt = metadata["interface"]["default_prompt"]
        self.assertIn("$cloudrelay-imagegen", prompt)


if __name__ == "__main__":
    unittest.main()
