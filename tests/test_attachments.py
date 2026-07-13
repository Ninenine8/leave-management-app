import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import app


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original_upload_dir = app.UPLOAD_DIR
        app.UPLOAD_DIR = Path(self.tmp.name) / "uploads"

    def tearDown(self):
        app.UPLOAD_DIR = self.original_upload_dir
        self.tmp.cleanup()

    def attachment(self, filename, content):
        return SimpleNamespace(filename=filename, file=io.BytesIO(content))

    def test_annual_leave_attachment_is_optional(self):
        self.assertIsNone(app.validate_and_store_attachment(None, "Annual Leave"))

    def test_off_in_lieu_attachment_is_optional(self):
        self.assertIsNone(app.validate_and_store_attachment(None, "Off-in-lieu"))

    def test_medical_leave_requires_attachment(self):
        with self.assertRaisesRegex(ValueError, "Supporting document is required for Medical Leave"):
            app.validate_and_store_attachment(None, "Medical Leave")

    def test_hospitalisation_leave_requires_attachment(self):
        with self.assertRaisesRegex(ValueError, "Supporting document is required for Hospitalisation Leave"):
            app.validate_and_store_attachment(None, "Hospitalisation Leave")

    def test_rejects_invalid_file_type(self):
        bad = self.attachment("script.exe", b"MZ fake")

        with self.assertRaisesRegex(ValueError, "PDF, JPG, or PNG"):
            app.validate_and_store_attachment(bad, "Annual Leave")

    def test_rejects_mismatched_file_signature(self):
        bad = self.attachment("document.pdf", b"not really a pdf")

        with self.assertRaisesRegex(ValueError, "valid PDF"):
            app.validate_and_store_attachment(bad, "Annual Leave")

    def test_rejects_file_larger_than_5mb(self):
        large = self.attachment("document.pdf", b"%PDF-" + b"x" * app.MAX_ATTACHMENT_BYTES)

        with self.assertRaisesRegex(ValueError, "5MB or smaller"):
            app.validate_and_store_attachment(large, "Annual Leave")

    def test_stores_pdf_with_safe_random_name(self):
        uploaded = self.attachment("../medical cert.pdf", b"%PDF- test")

        stored = app.validate_and_store_attachment(uploaded, "Medical Leave")

        self.assertIsNotNone(stored)
        self.assertNotIn("..", stored)
        self.assertTrue(stored.endswith(".pdf"))
        self.assertTrue((app.UPLOAD_DIR / stored).exists())

    def test_accepts_jpg_and_png(self):
        jpg = self.attachment("photo.jpg", b"\xff\xd8\xff\xe0data")
        png = self.attachment("image.png", b"\x89PNG\r\n\x1a\ndata")

        self.assertTrue(app.validate_and_store_attachment(jpg, "Annual Leave").endswith(".jpg"))
        self.assertTrue(app.validate_and_store_attachment(png, "Annual Leave").endswith(".png"))


if __name__ == "__main__":
    unittest.main()
