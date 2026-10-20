"""Offline checks for real reference-image uploads; no paid API calls."""

import base64
from email import policy
from email.parser import BytesParser
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts import generate_image as generator


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWZkAAAAASUVORK5CYII="
)
JPEG = b"\xff\xd8\xff\xe0" + b"jpeg-reference-fixture"
WEBP = b"RIFF\x16\x00\x00\x00WEBPVP8L" + b"webp-reference-fixture"


class Response:
    def __init__(self, status, payload):
        self.status = status
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def multipart_parts(request):
    content_type = request.get_header("Content-type")
    message = BytesParser(policy=policy.default).parsebytes(
        ("Content-Type: " + content_type + "\r\nMIME-Version: 1.0\r\n\r\n").encode()
        + request.data
    )
    return list(message.iter_parts())


class ReferenceImageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.first = self.root / "主体 参考.jpg"  # Content, not suffix, determines MIME.
        self.first.write_bytes(PNG)
        self.second = self.root / "style.jpeg"
        self.second.write_bytes(JPEG)
        self.third = self.root / "texture.webp"
        self.third.write_bytes(WEBP)

    def args(self, images):
        args = generator.build_parser().parse_args(
            ["--provider", "gpt", "--model", "gpt-image-2.5-flare",
             "--prompt", "保留主体，采用第二张画风"]
        )
        args.image = images
        return args

    def test_single_reference_is_uploaded_as_multipart_with_correct_mime(self):
        with mock.patch.object(
            generator._NO_REDIRECT_OPENER, "open",
            return_value=Response(202, {"task_id": "edit-123", "status": "queued"}),
        ) as opener, mock.patch("builtins.print"):
            task_id, _ = generator._submit_image("gpt", self.args([self.first]), "test-secret")
        self.assertEqual(task_id, "edit-123")
        opener.assert_called_once()
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, "https://cloudrelay.cn/v1/images/edits/async")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-secret")
        self.assertTrue(request.get_header("Content-type").startswith("multipart/form-data; boundary="))
        parts = multipart_parts(request)
        fields = {p.get_param("name", header="content-disposition"): p.get_payload(decode=True)
                  for p in parts if p.get_filename() is None}
        self.assertEqual(fields, {"model": b"gpt-image-2.5-flare",
                                 "prompt": "保留主体，采用第二张画风".encode(), "n": b"1"})
        files = [p for p in parts if p.get_filename() is not None]
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].get_param("name", header="content-disposition"), "image[]")
        self.assertEqual(files[0].get_content_type(), "image/png")
        self.assertEqual(files[0].get_payload(decode=True), PNG)

    def test_multiple_references_preserve_order_bytes_and_parameters_for_each_channel(self):
        for provider, model in (("gpt", "gpt-image-2.5-flare"),
                                ("gemini", "gemini-3.1-flash-image-preview"),
                                ("grok", "grok-imagine-image-2.0")):
            with self.subTest(provider=provider):
                args = self.args([self.first, self.second, self.third])
                args.provider, args.model = provider, model
                args.size, args.quality, args.count = "1536x1024", "high", 2
                with mock.patch.object(generator._NO_REDIRECT_OPENER, "open",
                                       return_value=Response(202, {"task_id": "edit-many"})) as opener, \
                     mock.patch("builtins.print"):
                    generator._submit_image(provider, args, "test-secret")
                request = opener.call_args.args[0]
                self.assertEqual(request.full_url, "https://cloudrelay.cn/v1/images/edits/async")
                parts = multipart_parts(request)
                files = [p for p in parts if p.get_filename() is not None]
                self.assertEqual([p.get_payload(decode=True) for p in files], [PNG, JPEG, WEBP])
                self.assertEqual([p.get_content_type() for p in files],
                                 ["image/png", "image/jpeg", "image/webp"])
                self.assertEqual([p.get_param("name", header="content-disposition") for p in files],
                                 ["image[]"] * 3)
                fields = {p.get_param("name", header="content-disposition"): p.get_payload(decode=True)
                          for p in parts if p.get_filename() is None}
                self.assertEqual(fields["model"], model.encode())
                self.assertEqual(fields["size"], b"1536x1024")
                self.assertEqual(fields["quality"], b"high")
                self.assertEqual(fields["n"], b"2")

    def test_cli_upload_poll_save_and_report(self):
        output = self.root / "out"
        discovery = {"platform": "openai", "image_generation_allowed": True, "data": [
            {"id": "gpt-image-2.5-flare", "kind": "image", "endpoint": "images", "async": True}]}
        completed = {"status": "completed", "result": {
            "data": [{"b64_json": base64.b64encode(PNG).decode()}], "usage": {"images": 1}}}
        with mock.patch.object(generator, "_api_key", return_value=("test-secret", "test-source")), \
             mock.patch.object(generator._NO_REDIRECT_OPENER, "open", side_effect=[
                 Response(200, discovery), Response(202, {"task_id": "edit-cli", "status": "queued"}),
                 Response(200, completed)]) as opener, \
             mock.patch.object(generator.time, "sleep"), mock.patch("builtins.print") as printer:
            result = generator.main(["--provider", "gpt", "--prompt", "参考第一张主体与第二张风格",
                                     "--image", str(self.first), "--image", str(self.second),
                                     "--output-dir", str(output)])
        self.assertEqual(result, 0)
        self.assertEqual([c.args[0].method for c in opener.call_args_list], ["GET", "POST", "GET"])
        self.assertTrue(opener.call_args_list[-1].args[0].full_url.endswith("/v1/images/tasks/edit-cli"))
        self.assertEqual(len(multipart_parts(opener.call_args_list[1].args[0])), 5)
        rendered = "\n".join(str(c.args[0]) for c in printer.call_args_list)
        parameters = json.loads(next(line.split("=", 1)[1] for line in rendered.splitlines()
                                     if line.startswith("PARAMETERS_JSON=")))
        self.assertEqual(parameters["input_images"], [str(self.first.resolve()), str(self.second.resolve())])
        self.assertEqual(parameters["input_image"], str(self.first.resolve()))
        self.assertEqual(parameters["endpoint"], "/v1/images/edits/async")
        self.assertEqual(parameters["reference_image_count"], 2)
        self.assertEqual(parameters["actual_sizes"], ["1x1"])
        self.assertEqual(Path(parameters["outputs"][0]).read_bytes(), PNG)
        self.assertNotIn("test-secret", rendered)
        self.assertNotIn(base64.b64encode(PNG).decode(), rendered)

    def test_bad_reference_stops_before_credentials_discovery_or_submission(self):
        empty = self.root / "empty.png"
        empty.write_bytes(b"")
        invalid = self.root / "fake.png"
        invalid.write_text("not an image", encoding="utf-8")
        for path in (self.root / "missing.png", self.root, empty, invalid):
            with self.subTest(path=path), \
                 mock.patch.object(generator, "_api_key") as key, \
                 mock.patch.object(generator._NO_REDIRECT_OPENER, "open") as opener, \
                 mock.patch("sys.stderr", new_callable=io.StringIO) as errors:
                result = generator.main(["--prompt", "test", "--image", str(path)])
                self.assertEqual(result, 1)
                self.assertIn("reference image", errors.getvalue().lower())
                key.assert_not_called()
                opener.assert_not_called()

    def test_unreadable_reference_is_a_handled_error(self):
        with mock.patch.object(Path, "read_bytes", side_effect=PermissionError("permission denied")), \
             mock.patch.object(generator, "_api_key") as key, \
             mock.patch.object(generator._NO_REDIRECT_OPENER, "open") as opener, \
             mock.patch("sys.stderr", new_callable=io.StringIO) as errors:
            result = generator.main(["--prompt", "test", "--image", str(self.first)])
        self.assertEqual(result, 1)
        self.assertIn("reference image", errors.getvalue().lower())
        key.assert_not_called()
        opener.assert_not_called()

    def test_edit_rejection_does_not_retry_or_fall_back_to_text_generation(self):
        for status in (400, 401, 403, 404, 429, 500):
            with self.subTest(status=status), \
                 mock.patch.object(generator, "_request", return_value=(status, {"error": {"message": "rejected"}})) as request:
                with self.assertRaises(generator.CloudRelayError):
                    generator._submit_image("gpt", self.args([self.first]), "test-secret")
                request.assert_called_once()
                self.assertEqual(request.call_args.args[1], "/v1/images/edits/async")


if __name__ == "__main__":
    unittest.main()
