from __future__ import annotations

import copy
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fire_audit.document_text import DocumentTextExtractor
from fire_audit.io_utils import sha256_file
from fire_audit.norms import NormativeIndex
from fire_audit.paths import get_paths
from fire_audit.validation import validate_source_reading


class NormReadingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = get_paths(Path(self.temp.name) / "app")
        self.index = NormativeIndex(self.paths)

    def corpus(self):
        with zipfile.ZipFile(Path(self.temp.name) / "fire-docs-main.zip", "w") as archive:
            archive.writestr("fire-docs-main/рабочая/СП.txt", "Контекст нормы [280]. " * 800)
            archive.writestr("fire-docs-main/письма ВНИИПО/280.txt", "Текст основного письма")
            archive.writestr("fire-docs-main/письма ВНИИПО/280.1.txt", "Продолжение письма")
            archive.writestr("fire-docs-main/письма ВНИИПО/280.2.txt", "Окончание письма")
            archive.writestr("fire-docs-main/письма ВНИИПО/Номенклатура.txt", "Описание [280]")
            archive.writestr("fire-docs-main/письма ВНИИПО/281.txt", "Другое письмо")
        return self.index.rebuild()

    def test_default_update_reads_letters_and_full_text_is_paginated_without_loss(self):
        updated = self.corpus()
        self.assertTrue(updated["full_text_coverage_complete"])
        family = self.index.find_letters("[280]")
        self.assertEqual(family["total_parts"], 3)
        self.assertEqual(len(family["nomenclature"]), 1)
        self.assertFalse(family["missing"])
        self.assertTrue(self.index.find_letters("999")["missing"])
        chunks, offset = [], 0
        while True:
            part = self.index.read_document("рабочая/СП.txt", offset=offset, max_chars=1000)
            chunks.append(part["text"])
            self.assertIn("280", part["letter_references_in_fragment"])
            if part["next_offset"] is None:
                break
            offset = part["next_offset"]
        self.assertGreater(len(chunks), 1)
        self.assertEqual("".join(chunks).count("[280]"), 800)
        page = self.index.read_document("рабочая/СП.txt", page=1)
        self.assertTrue(page["full_text_complete"])
        self.assertTrue(self.index.search("Продолжение", folder="письма ВНИИПО"))

    def test_unread_file_is_listed_and_cannot_be_marked_full_text(self):
        with zipfile.ZipFile(Path(self.temp.name) / "fire-docs-main.zip", "w") as archive:
            archive.writestr("fire-docs-main/рабочая/поврежденный.pdf", b"invalid")
        result = self.index.rebuild()
        self.assertFalse(result["full_text_coverage_complete"])
        missing = self.index.list_documents(needs_attention=True)
        self.assertEqual(missing["total"], 1)
        document = self.index.read_document(missing["documents"][0]["relative_path"])
        self.assertFalse(document["full_text_complete"])
        self.assertEqual(document["text"], "")

    def test_mixed_pdf_scanned_page_is_not_masked_by_text_on_other_page(self):
        from PIL import Image, ImageDraw
        from reportlab.pdfgen import canvas
        from reportlab.lib.utils import ImageReader
        path = Path(self.temp.name) / "mixed.pdf"
        image = Image.new("RGB", (100, 100), "white")
        ImageDraw.Draw(image).text((5, 20), "SCANNED", fill="black")
        pdf = canvas.Canvas(str(path))
        pdf.drawString(40, 800, "Digital text " * 20)
        pdf.showPage()
        pdf.drawImage(ImageReader(image), 40, 500, width=100, height=100)
        pdf.save()
        extractor = DocumentTextExtractor(self.paths.data / "text_cache")
        with patch.object(DocumentTextExtractor, "_ocr", return_value={
            2: {"page": 2, "text": "Текст второй отсканированной страницы", "error": ""}
        }) as ocr:
            result = extractor.extract(path, sha256_file(path))
        self.assertEqual(result["page_count"], 2)
        self.assertTrue(result["complete"])
        self.assertTrue(result["ocr_used"])
        self.assertEqual(result["pages"][1]["method"], "windows_ocr_ru")
        self.assertEqual(ocr.call_args.args[0][0]["page"], 2)

    def test_exactly_white_pdf_page_is_retained_as_blank_not_missing_text(self):
        from reportlab.pdfgen import canvas
        path = Path(self.temp.name) / "blank_back.pdf"
        pdf = canvas.Canvas(str(path))
        pdf.drawString(40, 800, "Letter text " * 20)
        pdf.showPage()
        pdf.showPage()
        pdf.save()
        extractor = DocumentTextExtractor(self.paths.data / "text_cache")
        with patch.object(DocumentTextExtractor, "_ocr", return_value={}) as ocr:
            result = extractor.extract(path, sha256_file(path))
        self.assertTrue(result["complete"])
        self.assertEqual(result["page_count"], 2)
        self.assertTrue(result["pages"][1]["blank_page"])
        self.assertEqual(ocr.call_args.args[0], [])

    def test_retry_only_reprocesses_unread_pages_of_unchanged_pdf(self):
        from PIL import Image, ImageDraw
        from reportlab.pdfgen import canvas
        from reportlab.lib.utils import ImageReader
        image = Image.new("RGB", (100, 100), "white")
        ImageDraw.Draw(image).text((5, 20), "SCANNED", fill="black")
        path = Path(self.temp.name) / "partial.pdf"
        pdf = canvas.Canvas(str(path))
        for _ in range(2):
            pdf.drawImage(ImageReader(image), 40, 500, width=100, height=100)
            pdf.showPage()
        pdf.save()
        extractor = DocumentTextExtractor(self.paths.data / "text_cache")
        with patch.object(DocumentTextExtractor, "_ocr", return_value={
            1: {"text": "Первая страница", "error": ""}, 2: {"text": "", "error": "not read"}
        }):
            self.assertFalse(extractor.extract(path, sha256_file(path))["complete"])
        with patch.object(DocumentTextExtractor, "_ocr", return_value={
            2: {"text": "Вторая страница", "error": ""}
        }) as ocr:
            result = extractor.extract(path, sha256_file(path))
        self.assertTrue(result["complete"])
        self.assertEqual([row["page"] for row in ocr.call_args.args[0]], [2])
        self.assertEqual(result["pages"][0]["text"], "Первая страница")

    def test_all_frames_of_tiff_and_all_images_of_word_are_processed(self):
        from PIL import Image
        from docx import Document
        first = Image.new("RGB", (100, 100), "white")
        second = Image.new("RGB", (100, 100), "gray")
        tif = Path(self.temp.name) / "letter.tif"
        first.save(tif, save_all=True, append_images=[second])
        extractor = DocumentTextExtractor(self.paths.data / "text_cache")
        with patch.object(DocumentTextExtractor, "_ocr", return_value={
            1: {"page": 1, "text": "Первая часть", "error": ""},
            2: {"page": 2, "text": "Вторая часть", "error": ""},
        }):
            result = extractor.extract(tif, sha256_file(tif))
        self.assertEqual(result["page_count"], 2)
        self.assertTrue(result["complete"])
        png = Path(self.temp.name) / "scan.png"
        first.save(png)
        word = Path(self.temp.name) / "image-only.docx"
        doc = Document()
        doc.add_picture(str(png))
        doc.save(word)
        with patch.object(DocumentTextExtractor, "_ocr", return_value={
            1: {"page": 1, "text": "Содержимое изображения в Word", "error": ""},
        }):
            result = extractor.extract(word, sha256_file(word))
        self.assertTrue(result["complete"], result["pages"])
        self.assertIn("Содержимое изображения", result["content"])

    def test_missing_letter_part_or_unverified_ocr_blocks_definitive_result(self):
        finding = {"id": "1", "decision": {"status": "violation"}, "analysis": {"source_reading": {
            "required_documents": [{"relative_path": "рабочая/СП.pdf", "source_sha256": "a" * 64,
                                    "pages_read": [1], "full_context_read": True, "ocr_used": True,
                                    "visual_verified": True, "visual_review_required": True, "unresolved_pages": []}],
            "norm_comments_letters_separated": True,
            "detected_letter_references": ["280"],
            "letter_checks": [{"reference": "280", "all_parts": ["280.pdf", "280.1.jpg"],
                              "read_parts": ["280.pdf", "280.1.jpg"], "date_number_and_current_norm_checked": True}],
            "order_2594_check": {"status": "included", "source_file": "перечень.pdf", "exact_item": "7.1.6"},
        }}}
        self.assertEqual(validate_source_reading(finding), [])
        bad = copy.deepcopy(finding)
        bad["analysis"]["source_reading"]["letter_checks"][0]["read_parts"].pop()
        self.assertTrue(any("не все части" in e for e in validate_source_reading(bad)))
        bad = copy.deepcopy(finding)
        bad["analysis"]["source_reading"]["required_documents"][0]["visual_verified"] = False
        self.assertTrue(any("OCR" in e for e in validate_source_reading(bad)))


if __name__ == "__main__":
    unittest.main()
