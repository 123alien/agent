import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from app.services.document_visual_service import analyze_document_visuals


class DocumentVisualServiceTests(unittest.TestCase):
    def test_detects_red_seal_candidate_with_page_and_bbox(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "response.pdf"
            image = Image.new("RGB", (600, 800), "white")
            draw = ImageDraw.Draw(image)
            draw.ellipse((380, 560, 520, 700), fill=(220, 20, 30))
            image.save(path, "PDF", resolution=100)
            image.close()

            result = analyze_document_visuals(path)

            self.assertEqual(result.analyzed_pages, 1)
            self.assertTrue(result.detections)
            detection = result.detections[0]
            self.assertEqual(detection.page, 1)
            self.assertEqual(detection.detection_type, "seal")
            self.assertEqual(len(detection.bbox), 4)
            self.assertGreaterEqual(detection.confidence, 0.8)

    def test_non_pdf_is_safely_skipped(self) -> None:
        result = analyze_document_visuals("sample.docx")
        self.assertEqual(result.analyzed_pages, 0)
        self.assertTrue(result.warnings)


if __name__ == "__main__":
    unittest.main()
