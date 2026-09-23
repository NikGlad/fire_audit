from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from .io_utils import read_json, write_json


EXTRACTOR_VERSION = 1
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}
TEXT_SUFFIXES = {".txt", ".md", ".csv", ".json", ".xml", ".html", ".htm"}
WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def native_tool(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    native = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/native"
    candidate = native / "poppler/Library/bin" / f"{name}.exe"
    return str(candidate) if candidate.exists() else None


def run_hidden(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.run(args, **kwargs)


def _page(number: int, text: str, method: str, **extra) -> dict[str, Any]:
    return {"page": number, "text": text.strip(), "method": method,
            "comments": [], "warnings": [], **extra}


class DocumentTextExtractor:
    def __init__(self, cache: Path):
        self.cache = cache

    def extract(self, path: Path, digest: str) -> dict[str, Any]:
        cache_path = self.cache / f"v{EXTRACTOR_VERSION}-{digest}.json"
        try:
            cached = read_json(cache_path, None)
        except (OSError, ValueError):
            # A damaged extraction cache is recoverable from the unchanged source.
            cached = None
        if cached and cached.get("source_sha256") != digest:
            cached = None
        if cached and cached.get("complete") and cached.get("source_sha256") == digest:
            return cached
        try:
            if path.suffix.lower() == ".pdf":
                pages = self._pdf(path, previous=cached)
            elif path.suffix.lower() in IMAGE_SUFFIXES:
                pages = self._images(path)
            elif path.suffix.lower() == ".docx":
                pages = self._docx(path)
            elif path.suffix.lower() in TEXT_SUFFIXES:
                raw = path.read_bytes()
                for encoding in ("utf-8-sig", "utf-16", "cp1251"):
                    try:
                        text = raw.decode(encoding)
                        break
                    except UnicodeError:
                        continue
                else:
                    raise ValueError("Cannot decode text")
                pages = [_page(1, text, "native_text", locator_kind="text")]
            else:
                pages = [_page(1, "", "unsupported", warnings=["unsupported_format"])]
        except Exception as exc:
            pages = [_page(1, "", "error", warnings=[f"{type(exc).__name__}: {exc}"])]
        reviews = read_json(self.cache.parent / "text_reviews" / f"{digest}.json", {})
        if reviews.get("source_sha256") == digest:
            for review in reviews.get("pages", []):
                if (review.get("classification") in {"no_text_content", "illustration"}
                        and review.get("visually_verified") is True and review.get("reason")
                        and review.get("image_path") and Path(review["image_path"]).is_file()):
                    for page in pages:
                        if page["page"] == review.get("page"):
                            if review["classification"] == "no_text_content" and not page["text"].strip():
                                page.update(method="visually_verified_no_text", blank_page=True, warnings=[],
                                            visual_review=review, visual_review_required=True)
                            elif review.get("description"):
                                page.update(method=page["method"] + "+visual_description_not_normative_text", warnings=[],
                                            text=page["text"] + "\n[Визуальное описание иллюстрации, не нормативный текст]\n" + review["description"],
                                            visual_review=review, visual_review_required=True)
        unresolved = [p["page"] for p in pages if p["warnings"] or (not p["text"] and not p.get("blank_page"))]
        # OCR and embedded graphics are always identified for subsequent visual review.
        content = "\n\n".join(
            f"[{p.get('locator_kind', 'page')} {p['page']}; {p['method']}]\n{p['text']}"
            + ("\n[Комментарии документа, не нормативный текст]\n" + "\n".join(p["comments"])
               if p["comments"] else "") for p in pages
        )
        has_text = any(p["text"].strip() for p in pages)
        ocr_used = any("ocr" in p["method"] for p in pages)
        result = {
            "extractor_version": EXTRACTOR_VERSION, "source_sha256": digest,
            "pages": pages, "page_count": len(pages), "complete": not unresolved and has_text,
            "unresolved_pages": unresolved, "ocr_used": ocr_used,
            "visual_review_required": ocr_used or any(p.get("visual_review_required") for p in pages),
            "content": content if has_text else "",
            "text_status": ("partial_text" if has_text else "unreadable") if unresolved or not has_text
                           else ("ocr_extracted" if ocr_used else "text_extracted"),
        }
        write_json(cache_path, result)
        return result

    @staticmethod
    def _ocr(items: list[dict]) -> dict[int, dict]:
        results = DocumentTextExtractor._run_ocr(items)
        def score(text: str) -> int:
            return len(re.findall(r"[А-Яа-яЁё]{3,}", text))
        def orientation_score(text: str) -> int:
            return score(text) * 10 + len(re.findall(r"\d{2,}", text))
        weak = [item for item in items if score(results.get(item["page"], {}).get("text", "")) < 8]
        if not weak:
            return results
        from PIL import Image
        with tempfile.TemporaryDirectory(prefix="norm-ocr-rotate-") as temp:
            rotated, origins = [], {}
            for item in weak:
                with Image.open(item["path"]) as image:
                    for angle in (90, 180, 270):
                        index = len(rotated) + 1
                        path = Path(temp) / f"{index}.png"
                        image.rotate(angle, expand=True).save(path)
                        rotated.append({"page": index, "path": str(path)})
                        origins[index] = item["page"]
            alternatives = DocumentTextExtractor._run_ocr(rotated)
            for index, candidate in alternatives.items():
                original = origins[index]
                if orientation_score(candidate.get("text", "")) > orientation_score(results.get(original, {}).get("text", "")):
                    results[original] = {**candidate, "page": original}
        return results

    @staticmethod
    def _run_ocr(items: list[dict]) -> dict[int, dict]:
        if not items:
            return {}
        if os.name != "nt":
            raise RuntimeError("Windows OCR is unavailable on this host")
        executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        with tempfile.TemporaryDirectory(prefix="norm-ocr-request-") as temp:
            manifest = Path(temp) / "images.json"
            write_json(manifest, items)
            completed = run_hidden(
                [str(executable), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                 str(Path(__file__).with_name("ocr_windows.ps1")), "-Manifest", str(manifest)],
                capture_output=True, encoding="utf-8", errors="replace",
                timeout=max(180, len(items) * 130),
            )
            if completed.returncode:
                raise RuntimeError(completed.stderr.strip() or "Windows OCR failed")
            values = json.loads(completed.stdout.lstrip("\ufeff"))
            return {int(p["page"]): p for p in values}

    @staticmethod
    def _apply_ocr(pages: list[dict], items: list[dict]) -> None:
        try:
            results = DocumentTextExtractor._ocr(items)
        except Exception as exc:
            results = {}
            error = f"{type(exc).__name__}: {exc}"
        else:
            error = "OCR returned no result"
        for item in items:
            page = pages[item["page"] - 1]
            result = results.get(item["page"], {})
            recognized = result.get("text", "").strip()
            if recognized:
                # Retain extracted text too: OCR does not replace the source text layer.
                native = page["text"]
                page["text"] = recognized if not native else native + "\n[Распознавание изображения страницы]\n" + recognized
                page["method"] = "native_and_ocr" if native else "windows_ocr_ru"
            else:
                page["warnings"].append(result.get("error") or error or "no_recognized_text")
            page["visual_review_required"] = True
            if result.get("error") and recognized:
                page["warnings"].append(result["error"])

    def _pdf(self, path: Path, previous: dict | None = None) -> list[dict]:
        from pypdf import PdfReader

        previous_pages = {p["page"]: p for p in (previous or {}).get("pages", [])}
        with path.open("rb") as handle:
            reader = PdfReader(handle, strict=False)
            native = []
            pdftotext = native_tool("pdftotext")
            if pdftotext:
                output = run_hidden([pdftotext, "-layout", "-enc", "UTF-8", str(path), "-"],
                                    capture_output=True, timeout=180)
                if output.returncode == 0:
                    native = output.stdout.decode("utf-8", errors="replace").split("\f")
            pages = []
            scan_pages = []
            for number, pdf_page in enumerate(reader.pages, 1):
                saved = previous_pages.get(number)
                if saved and not saved.get("warnings") and (saved.get("text") or saved.get("blank_page")):
                    pages.append(saved)
                    continue
                text = native[number - 1] if len(native) >= len(reader.pages) else (pdf_page.extract_text() or "")
                comments = []
                for ref in pdf_page.get("/Annots", []):
                    annotation = ref.get_object()
                    if annotation.get("/Contents"):
                        comments.append(str(annotation["/Contents"]))
                try:
                    resources = pdf_page.get("/Resources", {}).get_object()
                    objects = resources.get("/XObject", {}).get_object() if "/XObject" in resources else {}
                    has_image = self._has_images(objects, set())
                except Exception:
                    has_image = True
                item = _page(number, text, "native_text", comments=comments,
                             visual_review_required=has_image)
                pages.append(item)
                # Text elsewhere in a PDF must never mask an unread scanned page.
                if len(re.sub(r"\W", "", text)) < 100 or has_image:
                    scan_pages.append(number)
            with tempfile.TemporaryDirectory(prefix="norm-pdf-") as temp:
                items = []
                renderer = native_tool("pdftoppm")
                for number in scan_pages:
                    if not renderer:
                        pages[number - 1]["warnings"].append("PDF renderer unavailable")
                        continue
                    prefix = Path(temp) / f"page-{number}"
                    rendered = run_hidden(
                        [renderer, "-f", str(number), "-l", str(number), "-singlefile",
                         "-scale-to", "3000", "-png", str(path), str(prefix)],
                        capture_output=True, timeout=120,
                    )
                    if rendered.returncode:
                        pages[number - 1]["warnings"].append("PDF page rendering failed")
                        continue
                    image_path = prefix.with_suffix(".png")
                    from PIL import Image
                    with Image.open(image_path) as rendered_image:
                        exactly_white = rendered_image.convert("RGB").getextrema() == ((255, 255),) * 3
                    if exactly_white and not pages[number - 1]["text"].strip():
                        pages[number - 1].update(
                            method="blank_rendered_page", blank_page=True,
                            blank_check="all_rendered_pixels_exactly_white", visual_review_required=True,
                        )
                        continue
                    items.append({"page": number, "path": str(prefix.with_suffix(".png"))})
                self._apply_ocr(pages, items)
            return pages

    @staticmethod
    def _has_images(objects, seen: set) -> bool:
        for ref in objects.values():
            key = (getattr(ref, "idnum", None), getattr(ref, "generation", None))
            if key[0] is not None:
                if key in seen:
                    continue
                seen.add(key)
            obj = ref.get_object()
            if obj.get("/Subtype") == "/Image":
                return True
            if obj.get("/Subtype") == "/Form" and "/Resources" in obj:
                resources = obj["/Resources"].get_object()
                if "/XObject" in resources and DocumentTextExtractor._has_images(resources["/XObject"].get_object(), seen):
                    return True
        return False

    def _images(self, path: Path) -> list[dict]:
        from PIL import Image, ImageOps, ImageSequence

        pages = []
        with tempfile.TemporaryDirectory(prefix="norm-image-") as temp:
            items = []
            with Image.open(path) as image:
                for number, frame in enumerate(ImageSequence.Iterator(image), 1):
                    frame = ImageOps.exif_transpose(frame).convert("RGB")
                    frame.thumbnail((3000, 3000))
                    output = Path(temp) / f"{number}.png"
                    frame.save(output)
                    pages.append(_page(number, "", "image"))
                    items.append({"page": number, "path": str(output)})
            self._apply_ocr(pages, items)
        return pages

    def _docx(self, path: Path) -> list[dict]:
        with zipfile.ZipFile(path) as archive:
            pages = []
            for name in archive.namelist():
                if name == "word/document.xml" or re.fullmatch(r"word/(header\d+|footer\d+|footnotes|endnotes|comments).xml", name):
                    root = ET.fromstring(archive.read(name))
                    paragraphs = ["".join(p.itertext()) for p in root.iter(WORD_NS + "p")]
                    text = "\n".join(paragraphs)
                    if text.strip():
                        pages.append(_page(len(pages) + 1, text, "docx_text", locator_kind=name))
            with tempfile.TemporaryDirectory(prefix="norm-docx-media-") as temp:
                for name in archive.namelist():
                    if name.startswith("word/media/"):
                        target = Path(temp) / Path(name).name
                        target.write_bytes(archive.read(name))
                        if target.suffix.lower() in IMAGE_SUFFIXES:
                            for item in self._images(target):
                                item.update(page=len(pages) + 1, locator_kind=name)
                                pages.append(item)
                        else:
                            pages.append(_page(len(pages) + 1, "", "unsupported",
                                               locator_kind=name, warnings=["embedded_graphic_requires_visual_reading"]))
            return pages or [_page(1, "", "empty_document", warnings=["no_document_content"])]
