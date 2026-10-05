from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import check_publication


class PublicationImageTests(unittest.TestCase):
    def test_reviewed_hardware_photo_passes(self):
        photo = check_publication.ROOT / "docs/images/power-one-pvi-5000-outd-us-z-front.jpg"
        self.assertEqual(check_publication.check([photo]), [])

    def test_changing_photo_or_restoring_metadata_requires_review(self):
        relative = Path("docs/images/power-one-pvi-5000-outd-us-z-front.jpg")
        original = (check_publication.ROOT / relative).read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            photo = root / relative
            photo.parent.mkdir(parents=True)
            # A JPEG comment can carry private data without changing the pixels.
            comment = b"private equipment notes"
            marker = b"\xff\xfe" + (len(comment) + 2).to_bytes(2, "big") + comment
            photo.write_bytes(original[:2] + marker + original[2:])
            with patch.object(check_publication, "ROOT", root):
                failures = check_publication.check([photo])
            self.assertEqual(len(failures), 1)
            self.assertIn("unreviewed or changed image", failures[0])

    def test_new_image_is_not_approved_by_extension_or_text_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, content in (("other.jpg", b"\xff\xd8\xff\xd9"),
                                  ("other.PNG", b"looks like harmless text")):
                with self.subTest(name=name), patch.object(check_publication, "ROOT", root):
                    path = root / name
                    path.write_bytes(content)
                    self.assertIn("unreviewed or changed image", check_publication.check([path])[0])

    def test_other_binary_artifact_fails_without_crashing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "unexpected.bin"
            path.write_bytes(b"\x00\xff\xfe")
            with patch.object(check_publication, "ROOT", root):
                self.assertIn("unreviewed binary artifact", check_publication.check([path])[0])
