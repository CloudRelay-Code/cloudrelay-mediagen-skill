from __future__ import annotations

from pathlib import Path
import os
import stat
import tempfile
import unittest
from unittest import mock

from scripts import configure_api_key
from scripts import generate_image


class CredentialTests(unittest.TestCase):
    def test_provider_environment_names_and_secret_files_are_distinct(self) -> None:
        self.assertEqual(
            configure_api_key.PROVIDER_ENV_NAMES,
            {
                "gpt": "CLOUDRELAY_GPT_IMAGE_API_KEY",
                "gemini": "CLOUDRELAY_GEMINI_API_KEY",
                "grok": "CLOUDRELAY_GROK_API_KEY",
            },
        )
        self.assertEqual(
            generate_image.GROUP_LABELS,
            {"gpt": "生图专用", "gemini": "gemini", "grok": "grok"},
        )
        self.assertEqual(
            {
                configure_api_key._secret_file(provider).name
                for provider in configure_api_key.PROVIDERS
            },
            {
                "imagegen-api-key-gpt",
                "imagegen-api-key-gemini",
                "imagegen-api-key-grok",
            },
        )

    def test_key_validation_trims_outer_whitespace(self) -> None:
        self.assertEqual(configure_api_key._validate_key("  test-key-value\n"), "test-key-value")

    def test_key_validation_rejects_internal_whitespace(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not contain whitespace"):
            configure_api_key._validate_key("test key value")

    def test_key_stdin_reader_does_not_use_command_line_arguments(self) -> None:
        with mock.patch.object(configure_api_key.sys, "stdin") as stdin:
            stdin.read.return_value = "test-key-value\n"
            self.assertEqual(configure_api_key._read_key_from_stdin(), "test-key-value")

    @unittest.skipUnless(os.name != "nt", "POSIX secret-file persistence is not used on Windows")
    def test_provider_persistence_writes_outside_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            for provider in configure_api_key.PROVIDERS:
                with self.subTest(provider=provider):
                    secret_file = (
                        Path(temporary)
                        / "cloudrelay"
                        / f"imagegen-api-key-{provider}"
                    )
                    with mock.patch.object(
                        configure_api_key,
                        "_secret_file",
                        return_value=secret_file,
                    ):
                        result = configure_api_key._persist_posix_secret_file(
                            f"{provider}-test-key", provider
                        )
                    self.assertEqual(result, secret_file)
                    self.assertEqual(
                        secret_file.read_text(encoding="utf-8"),
                        f"{provider}-test-key\n",
                    )
                    if os.name != "nt":
                        self.assertEqual(stat.S_IMODE(secret_file.stat().st_mode), 0o600)
                        self.assertEqual(stat.S_IMODE(secret_file.parent.stat().st_mode), 0o700)

    def test_generator_reads_xdg_secret_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            secret_file = Path(temporary) / "cloudrelay" / "imagegen-api-key-grok"
            secret_file.parent.mkdir(parents=True)
            secret_file.write_text("test-key-value\n", encoding="utf-8")
            with mock.patch.dict(
                generate_image.os.environ,
                {"XDG_CONFIG_HOME": temporary},
                clear=False,
            ):
                self.assertEqual(
                    generate_image._read_posix_secret_file("grok"),
                    "test-key-value",
                )

    def test_empty_secret_file_is_treated_as_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            secret_file = Path(temporary) / "cloudrelay" / "imagegen-api-key-gpt"
            secret_file.parent.mkdir(parents=True)
            secret_file.write_text("\n", encoding="utf-8")
            with mock.patch.dict(
                generate_image.os.environ,
                {"XDG_CONFIG_HOME": temporary},
                clear=False,
            ):
                self.assertIsNone(generate_image._read_posix_secret_file("gpt"))

    def test_api_keys_are_isolated_by_provider(self) -> None:
        with mock.patch.dict(
            generate_image.os.environ,
            {
                "CLOUDRELAY_GPT_IMAGE_API_KEY": "gpt-key",
                "CLOUDRELAY_GEMINI_API_KEY": "gemini-key",
                "CLOUDRELAY_GROK_API_KEY": "grok-key",
            },
            clear=True,
        ), mock.patch.object(
            generate_image, "_read_windows_user_environment", return_value=None
        ):
            self.assertEqual(generate_image._api_key("gpt"), "gpt-key")
            self.assertEqual(generate_image._api_key("gemini"), "gemini-key")
            self.assertEqual(generate_image._api_key("grok"), "grok-key")

    def test_gpt_does_not_fall_back_to_other_channel_key(self) -> None:
        with mock.patch.dict(
            generate_image.os.environ,
            {
                "CLOUDRELAY_GPT_IMAGE_API_KEY": "",
                "CLOUDRELAY_GEMINI_API_KEY": "gemini-key",
                "CLOUDRELAY_GROK_API_KEY": "grok-key",
            },
            clear=True,
        ), mock.patch.object(
            generate_image, "_read_windows_user_environment", return_value=None
        ), mock.patch.object(
            generate_image, "_secret_file", return_value=Path("missing")
        ):
            with self.assertRaisesRegex(
                generate_image.CloudRelayError,
                "CLOUDRELAY_GPT_IMAGE_API_KEY",
            ):
                generate_image._api_key("gpt")

    def test_legacy_generic_environment_key_is_not_used(self) -> None:
        with mock.patch.dict(
            generate_image.os.environ,
            {"CLOUDRELAY_IMAGE_API_KEY": "legacy-key"},
            clear=True,
        ), mock.patch.object(
            generate_image, "_read_windows_user_environment", return_value=None
        ), mock.patch.object(
            generate_image, "_secret_file", return_value=Path("missing")
        ):
            with self.assertRaisesRegex(
                generate_image.CloudRelayError,
                "CLOUDRELAY_GPT_IMAGE_API_KEY",
            ):
                generate_image._api_key("gpt")


if __name__ == "__main__":
    unittest.main()
