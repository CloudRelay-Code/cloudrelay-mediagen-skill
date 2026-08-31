from __future__ import annotations

import json
from pathlib import Path
import unittest
from unittest import mock

from scripts import generate_image


class UnifiedProtocolTests(unittest.TestCase):
    def _args(self, provider: str) -> object:
        model = {
            "gpt": "gpt-image-2",
            "gemini": "gemini-3.1-flash-image-preview",
            "grok": "grok-imagine-image-2.0",
        }[provider]
        return generate_image.build_parser().parse_args(
            [
                "--provider",
                provider,
                "--prompt",
                "a test image",
                "--model",
                model,
                "--count",
                "2",
            ]
        )

    def test_channel_defaults_and_gemini_model_gate(self) -> None:
        self.assertEqual(generate_image._resolve_provider("auto", None), "gpt")
        self.assertEqual(generate_image._resolve_model("gpt", None), "gpt-image-2")
        self.assertEqual(
            generate_image._resolve_model("grok", None), "grok-imagine-image-2.0"
        )
        for model in generate_image.GEMINI_MODELS:
            with self.subTest(model=model):
                self.assertEqual(generate_image._resolve_model("gemini", model), model)
        with self.assertRaisesRegex(generate_image.CloudRelayError, "explicit confirmed model"):
            generate_image._resolve_model("gemini", None)
        with self.assertRaisesRegex(generate_image.CloudRelayError, "Unsupported Gemini"):
            generate_image._resolve_model("gemini", "gemini-2.5-flash")

    def test_each_channel_submits_the_same_async_request_shape(self) -> None:
        for provider in generate_image.PROVIDERS:
            with self.subTest(provider=provider):
                args = self._args(provider)
                with mock.patch.object(
                    generate_image,
                    "_request",
                    return_value=(202, {"task_id": "task-123", "status": "queued"}),
                ) as request:
                    task_id, submitted = generate_image._submit_image(
                        provider, args, "channel-secret"
                    )

                self.assertEqual(task_id, "task-123")
                self.assertEqual(submitted["status"], "queued")
                request.assert_called_once_with(
                    "POST",
                    generate_image.IMAGE_SUBMIT_PATH,
                    "channel-secret",
                    {
                        "model": args.model,
                        "prompt": "a test image",
                        "n": 2,
                    },
                    timeout=60,
                )

    def test_discovery_requires_image_async_model_on_shared_endpoint(self) -> None:
        for provider in generate_image.PROVIDERS:
            with self.subTest(provider=provider):
                model = self._args(provider).model
                payload = {
                    "platform": provider,
                    "image_generation_allowed": True,
                    "data": [
                        {
                            "id": model,
                            "kind": "image",
                            "endpoint": "images",
                            "async": True,
                        }
                    ],
                }
                with mock.patch.object(
                    generate_image, "_request", return_value=(200, payload)
                ) as request:
                    spec, discovery = generate_image._discover_model(
                        provider, model, "channel-secret"
                    )
                self.assertEqual(spec["id"], model)
                self.assertIs(discovery, payload)
                request.assert_called_once_with(
                    "GET",
                    generate_image.MEDIA_MODELS_PATH,
                    "channel-secret",
                    timeout=60,
                )

                for invalid_spec in (
                    {**payload["data"][0], "kind": "text"},
                    {**payload["data"][0], "endpoint": "chat"},
                    {**payload["data"][0], "async": False},
                ):
                    with self.subTest(provider=provider, invalid=invalid_spec):
                        with mock.patch.object(
                            generate_image,
                            "_request",
                            return_value=(
                                200,
                                {**payload, "data": [invalid_spec]},
                            ),
                        ):
                            with self.assertRaises(generate_image.CloudRelayError):
                                generate_image._discover_model(
                                    provider, model, "channel-secret"
                                )

    def test_discovery_rejects_disabled_permission_and_empty_models(self) -> None:
        model = "gpt-image-2"
        for payload in (
            {
                "platform": "gpt",
                "image_generation_allowed": False,
                "data": [{"id": model, "kind": "image", "endpoint": "images", "async": True}],
            },
            {"platform": "gpt", "image_generation_allowed": True, "data": []},
        ):
            with self.subTest(payload=payload):
                with mock.patch.object(
                    generate_image, "_request", return_value=(200, payload)
                ):
                    with self.assertRaises(generate_image.CloudRelayError):
                        generate_image._discover_model("gpt", model, "channel-secret")

    def test_request_uses_bearer_without_redirect_forwarding(self) -> None:
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({"ok": True}).encode("utf-8")

        with mock.patch.object(
            generate_image._NO_REDIRECT_OPENER, "open", return_value=Response()
        ) as opener:
            status, payload = generate_image._request(
                "GET", generate_image.MEDIA_MODELS_PATH, "channel-secret"
            )
        request = opener.call_args.args[0]
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"ok": True})
        self.assertEqual(request.full_url, "https://cloudrelay.cn/v1/media/models")
        self.assertEqual(request.get_header("Authorization"), "Bearer channel-secret")

    def test_auto_fields_are_omitted_and_explicit_values_are_reported(self) -> None:
        args = self._args("gpt")
        self.assertEqual(
            generate_image._image_payload(args),
            {"model": "gpt-image-2", "prompt": "a test image", "n": 2},
        )
        args.size = "1536x1024"
        args.quality = "high"
        self.assertEqual(
            generate_image._image_payload(args),
            {
                "model": "gpt-image-2",
                "prompt": "a test image",
                "n": 2,
                "size": "1536x1024",
                "quality": "high",
            },
        )

        with mock.patch.object(
            generate_image,
            "_save_images",
            return_value=[Path("C:/images/task-123_0.png")],
        ), mock.patch("builtins.print") as printer:
            generate_image._save_and_report(
                args,
                "gpt",
                "CLOUDRELAY_GPT_IMAGE_API_KEY",
                "task-123",
                [{"b64_json": "aGVsbG8="}],
                {"images": 1},
                {"kind": "image", "endpoint": "images", "async": True},
                {"platform": "cloudrelay", "image_generation_allowed": True},
                "secret-key",
            )
        rendered = "\n".join(str(call.args[0]) for call in printer.call_args_list)
        self.assertIn("Generation parameters:", rendered)
        self.assertIn("1536x1024", rendered)
        self.assertIn("gpt-image-2", rendered)
        self.assertNotIn("secret-key", rendered)
        parameters_line = next(
            line for line in rendered.splitlines() if line.startswith("PARAMETERS_JSON=")
        )
        parameters = json.loads(parameters_line.removeprefix("PARAMETERS_JSON="))
        self.assertEqual(parameters["provider"], "gpt")
        self.assertEqual(parameters["credential_group"], "生图专用")
        self.assertEqual(parameters["count_requested"], 2)
        self.assertEqual(parameters["count_saved"], 1)
        self.assertEqual(parameters["transport"], "async")
        self.assertEqual(parameters["endpoint"], "/v1/images/generations/async")


if __name__ == "__main__":
    unittest.main()
