"""Offline regression checks for the size sent to the paid generation API."""

import argparse
from pathlib import Path
import struct
import tempfile
import unittest

from scripts import generate_image as generator


class ResolutionTests(unittest.TestCase):
    def test_dimension_headers(self):
        fixtures = {
            "png": b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 137, 91),
            "gif": b"GIF89a" + struct.pack("<HH", 137, 91),
            "jpeg": b"\xff\xd8\xff\xc0" + struct.pack(">HBHH", 7, 8, 91, 137),
            "webp": b"RIFF" + struct.pack("<I", 22) + b"WEBPVP8X" + struct.pack("<I", 10) + b"\0" * 4 + (136).to_bytes(3, "little") + (90).to_bytes(3, "little"),
        }
        with tempfile.TemporaryDirectory() as directory:
            for name, data in fixtures.items():
                with self.subTest(format=name):
                    path = Path(directory) / name
                    path.write_bytes(data)
                    self.assertEqual(generator._image_dimensions(path), (137, 91))
            path.write_bytes(b"not an image")
            self.assertIsNone(generator._image_dimensions(path))

    def test_presets_and_explicit_sizes_reach_payload(self):
        for supplied, expected in (
            ("2k", "2048x2048"), ("2K", "2048x2048"),
            ("4k", "4096x4096"), ("4K", "4096x4096"),
            ("2048x2048", "2048x2048"), ("4096x4096", "4096x4096"),
            ("1024x1024", "1024x1024"), ("1536x1024", "1536x1024"),
            ("1024x1536", "1024x1536"),
            ("2160x2160", "2160x2160"), ("3840x2160", "3840x2160"),
            ("2160x3840", "2160x3840"), ("2560x1440", "2560x1440"),
        ):
            with self.subTest(size=supplied):
                args = generator.build_parser().parse_args(
                    ["--prompt", "test", "--size", supplied, "--quality", "high"]
                )
                payload = generator._image_payload(args)
                self.assertEqual(payload["size"], expected)
                self.assertEqual(payload["quality"], "high")

    def test_auto_omits_optional_fields(self):
        args = generator.build_parser().parse_args(["--prompt", "test"])
        self.assertNotIn("size", generator._image_payload(args))
        self.assertNotIn("quality", generator._image_payload(args))

    def test_invalid_size_rejected_before_network(self):
        for size in ("8k", "4096x0", "invalid"):
            with self.subTest(size=size), self.assertRaises(argparse.ArgumentTypeError):
                generator._parse_size(size)


if __name__ == "__main__":
    unittest.main()
