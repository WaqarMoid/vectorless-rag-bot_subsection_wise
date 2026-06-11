from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from pdf2image import convert_from_path, pdfinfo_from_path
from pdf2image.exceptions import PDFInfoNotInstalledError, PDFPageCountError, PDFSyntaxError
from pypdf import PdfReader
import pytesseract
from pytesseract import TesseractNotFoundError

from app.models import PageRecord


class PDFLoader:
    def __init__(self) -> None:
        pass

    def load(self, pdf_path: Path, book_id: str) -> list[PageRecord]:
        cache_dir = pdf_path.parent / "data"
        cache_dir.mkdir(exist_ok=True)
        cache_path = cache_dir / f"ocr_cache_{book_id}.json"

        if cache_path.exists():
            print(f"[PDFLoader] Loading OCR cache for '{book_id}' from {cache_path.name}...", flush=True)
            try:
                with cache_path.open("r", encoding="utf-8") as f:
                    cached_data = json.load(f)
                results = []
                for item in cached_data:
                    results.append(PageRecord(**item))
                print(f"[PDFLoader] Successfully loaded {len(results)} pages from cache.", flush=True)
                return results
            except Exception as e:
                print(f"[PDFLoader] Error loading OCR cache: {e}. Re-running OCR...", flush=True)

        reader = PdfReader(str(pdf_path))
        book_title = self._resolve_book_title(reader, pdf_path, book_id)

        # Check first 5 pages for standard text to decide if we need OCR
        has_std_text = False
        for i in range(min(5, len(reader.pages))):
            text = reader.pages[i].extract_text() or ""
            if text.strip():
                has_std_text = True
                break

        if has_std_text:
            print(f"[PDFLoader] Standard text found in {pdf_path.name}. Extracting text from PDF...", flush=True)
            line_pages = self._extract_line_pages(reader)
        else:
            print(f"[PDFLoader] No standard text detected on first 5 pages of {pdf_path.name}. Running OCR fallback...", flush=True)
            line_pages = self._ocr_line_pages(pdf_path)
            
        if not self._has_text(line_pages):
            raise ValueError(
                f"No extractable text found in {pdf_path.name}, even after OCR. "
                "Ensure Tesseract and Poppler are installed and on your PATH."
            )

        repeating_top, repeating_bottom = self._detect_repeating_lines(line_pages)

        results: list[PageRecord] = []
        for i, lines in enumerate(line_pages, start=1):
            cleaned_lines = [
                line for line in lines 
                if line not in repeating_top and line not in repeating_bottom
            ]
            text = " ".join(cleaned_lines)
            results.append(
                PageRecord(
                    book_id=book_id,
                    book_title=book_title,
                    page_number=i,
                    page_end=i,
                    text=text,
                    node_id="",
                    chapter_title="",
                    subsection_title="",
                )
            )

        # Save to cache
        try:
            with cache_path.open("w", encoding="utf-8") as f:
                json.dump([p.to_dict() for p in results], f, ensure_ascii=False, indent=2)
            print(f"[PDFLoader] Saved OCR cache for '{book_id}' to {cache_path.name}", flush=True)
        except Exception as e:
            print(f"[PDFLoader] Failed to save OCR cache: {e}", flush=True)

        return results

    def _extract_line_pages(self, reader: PdfReader) -> list[list[str]]:
        line_pages: list[list[str]] = []
        for page in reader.pages:
            text = page.extract_text() or ""
            lines = [self._normalize_line(line) for line in text.splitlines()]
            lines = [line for line in lines if line]
            line_pages.append(lines)
        return line_pages

    def _ocr_line_pages(self, pdf_path: Path) -> list[list[str]]:
        try:
            info = pdfinfo_from_path(str(pdf_path))
        except PDFInfoNotInstalledError as exc:
            raise ValueError(
                "OCR requires Poppler (pdfinfo) installed and available on PATH."
            ) from exc
        except (PDFPageCountError, PDFSyntaxError) as exc:
            raise ValueError(f"Failed to read PDF metadata for OCR: {exc}") from exc

        total_pages = int(info.get("Pages", 0) or 0)
        if total_pages <= 0:
            return []

        line_pages: list[list[str]] = [[] for _ in range(total_pages)]
        batch_size = 25
        print(f"[PDFLoader] Starting batched OCR for {pdf_path.name} (Total pages: {total_pages}, Batch size: {batch_size})", flush=True)

        for batch_start in range(1, total_pages + 1, batch_size):
            batch_end = min(batch_start + batch_size - 1, total_pages)
            print(f"[PDFLoader] Converting PDF pages {batch_start} to {batch_end} to images...", flush=True)
            try:
                images = convert_from_path(
                    str(pdf_path),
                    dpi=150,
                    first_page=batch_start,
                    last_page=batch_end,
                )
            except PDFInfoNotInstalledError as exc:
                raise ValueError(
                    "OCR requires Poppler installed and available on PATH."
                ) from exc
            except (PDFPageCountError, PDFSyntaxError) as exc:
                raise ValueError(f"Failed to render PDF pages {batch_start}-{batch_end}: {exc}") from exc

            for idx, img in enumerate(images):
                page_number = batch_start + idx
                if page_number > total_pages:
                    break
                print(f"[PDFLoader] OCRing page {page_number}/{total_pages}...", flush=True)
                try:
                    text = pytesseract.image_to_string(img, lang="eng")
                except TesseractNotFoundError as exc:
                    raise ValueError(
                        "OCR requires Tesseract installed and available on PATH."
                    ) from exc

                lines = [self._normalize_line(line) for line in text.splitlines()]
                lines = [line for line in lines if line]
                line_pages[page_number - 1] = lines

        return line_pages

    def _has_text(self, line_pages: list[list[str]]) -> bool:
        return any(lines for lines in line_pages)

    def _resolve_book_title(self, reader: PdfReader, pdf_path: Path, fallback: str) -> str:
        meta = reader.metadata or {}
        title = str(meta.get("/Title") or "").strip()
        if not title:
            # Fallback to file name without suffix and cleaned
            name = pdf_path.stem
            name = name.replace("_", " ").replace("-", " ")
            title = " ".join(word.capitalize() for word in name.split())
        return title

    def _normalize_line(self, line: str) -> str:
        # Normalize spaces and strip
        return " ".join(line.split()).strip()

    def _detect_repeating_lines(
        self, 
        line_pages: list[list[str]], 
        threshold: float = 0.3
    ) -> tuple[set[str], set[str]]:
        """
        Detects repeating headers and footers (lines appearing in more than 30% of pages).
        """
        total = len(line_pages)
        if total < 5:
            return set(), set()

        top_lines: list[str] = []
        bottom_lines: list[str] = []

        for lines in line_pages:
            if not lines:
                continue
            # Header candidate (first 2 lines)
            top_lines.extend(lines[:2])
            # Footer candidate (last 2 lines)
            bottom_lines.extend(lines[-2:])

        top_counts = Counter(top_lines)
        bottom_counts = Counter(bottom_lines)

        repeating_top = {line for line, count in top_counts.items() if count / total > threshold}
        repeating_bottom = {line for line, count in bottom_counts.items() if count / total > threshold}

        return repeating_top, repeating_bottom
