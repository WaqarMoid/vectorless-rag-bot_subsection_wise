from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from app.config import Settings
from app.llm.mistral_client import MistralClient
from app.models import PageRecord, TreeNode


class PageIndexBuilder:
    def __init__(self, settings: Settings, llm: MistralClient) -> None:
        self.settings = settings
        self.llm = llm

    def _safe_int(self, val: Any, default: int = 1) -> int:
        if val is None:
            return default
        try:
            if isinstance(val, str):
                cleaned_val = re.sub(r"\D", "", val)
                if cleaned_val:
                    return int(cleaned_val)
            return int(val)
        except (ValueError, TypeError):
            return default

    def build_tree(self, pages: list[PageRecord]) -> tuple[TreeNode, list[PageRecord]]:
        # Group raw physical page records by book
        by_book: dict[str, list[PageRecord]] = {}
        for page in pages:
            by_book.setdefault(page.book_id, []).append(page)

        book_nodes: list[TreeNode] = []
        all_chunks: list[PageRecord] = []

        for book_id, book_pages in by_book.items():
            book_pages = sorted(book_pages, key=lambda p: p.page_number)
            print(f"\n[PageIndexBuilder] === Processing book {book_id} ({len(book_pages)} physical pages) ===")
            
            # Step 1: Extract and parse TOC from the first 22 pages
            print(f"[PageIndexBuilder] Step 1/3: Extracting Table of Contents from first 22 pages...")
            toc_text = " ".join(p.text for p in book_pages[:22])
            parsed_toc = self._extract_toc(toc_text, book_pages[0].book_title or book_id)
            book_title = parsed_toc.get("book_title") or book_pages[0].book_title or book_id
            print(f"[PageIndexBuilder] Extracted title: '{book_title}'")
            
            # Step 2: Map logical page numbers to physical pages
            print(f"[PageIndexBuilder] Step 2/3: Mapping logical page numbers to physical pages...")
            flat_subsections = []
            for ch in parsed_toc.get("chapters", []):
                ch_title = ch.get("chapter_title", "Untitled Chapter")
                ch_num = ch.get("chapter_number", "")
                full_ch_title = f"Chapter {ch_num}: {ch_title}" if ch_num else ch_title
                for sub in ch.get("subsections", []):
                    flat_subsections.append((full_ch_title, ch_num, sub))

            # Locate where subsection numbers and title words first appear on physical pages
            detected_pages = {}
            for ch_title, ch_num, sub in flat_subsections:
                sub_num = sub.get("subsection_number", "")
                sub_title = sub.get("subsection_title", "")
                if not sub_num:
                    continue
                # Clean title words for matching
                title_words = [w.lower() for w in re.findall(r"\b\w{3,}\b", sub_title)]
                
                found_page = None
                for p in book_pages:
                    # Skip the first few pages containing TOC to avoid false positives
                    if p.page_number <= 3:
                        continue
                    text_lower = p.text.lower()
                    # Check if subsection number is present on the page
                    num_match = re.search(r"\b" + re.escape(sub_num) + r"\b", p.text) or (sub_num in p.text)
                    if num_match:
                        match_count = sum(1 for w in title_words if w in text_lower)
                        if (not title_words) or (len(title_words) == 1 and match_count >= 1) or (match_count >= min(2, len(title_words))):
                            found_page = p.page_number
                            break
                if found_page is not None:
                    detected_pages[sub_num] = found_page

            # Compute consensus offset
            offsets = []
            for ch_title, ch_num, sub in flat_subsections:
                sub_num = sub.get("subsection_number", "")
                logical_start = sub.get("logical_page_start")
                if sub_num in detected_pages and logical_start is not None:
                    offset = detected_pages[sub_num] - self._safe_int(logical_start)
                    if offset >= 0:
                        offsets.append(offset)

            if offsets:
                consensus_offset = Counter(offsets).most_common(1)[0][0]
                print(f"[PageIndexBuilder] Consensus page offset for '{book_title}': {consensus_offset}")
            else:
                consensus_offset = 0
                print(f"[PageIndexBuilder] No consensus offset detected for '{book_title}'. Defaulting to 0.")

            # Assign physical start pages
            for ch_title, ch_num, sub in flat_subsections:
                logical_start = sub.get("logical_page_start")
                p_start = self._safe_int(logical_start, 1) + consensus_offset
                p_start = max(1, min(p_start, len(book_pages)))
                sub["physical_page_start"] = p_start

            # Assign physical end pages
            for idx, (ch_title, ch_num, sub) in enumerate(flat_subsections):
                p_start = sub["physical_page_start"]
                if idx < len(flat_subsections) - 1:
                    next_p_start = flat_subsections[idx + 1][2]["physical_page_start"]
                    p_end = next_p_start - 1
                else:
                    p_end = len(book_pages)
                p_end = max(p_start, min(p_end, len(book_pages)))
                sub["physical_page_end"] = p_end

            # Step 3: Create TreeNode hierarchy and build subsection Chunks
            print(f"[PageIndexBuilder] Step 3/3: Generating summaries and building chunks...")
            chapter_nodes: list[TreeNode] = []
            
            # Group flat subsections back into chapters
            ch_subsections: dict[str, list[dict]] = {}
            ch_numbers: dict[str, str] = {}
            for ch_title, ch_num, sub in flat_subsections:
                ch_subsections.setdefault(ch_title, []).append(sub)
                ch_numbers[ch_title] = ch_num

            ch_index = 1
            for ch_title, subs in ch_subsections.items():
                ch_num = ch_numbers[ch_title]
                sub_nodes: list[TreeNode] = []
                sub_index = 1
                total_subs = len(subs)
                
                for idx_sub, sub in enumerate(subs, start=1):
                    sub_num = sub.get("subsection_number", f"{ch_num}.{sub_index}" if ch_num else f"{ch_index}.{sub_index}")
                    sub_title = sub.get("subsection_title", "Untitled Subsection")
                    p_start = sub["physical_page_start"]
                    p_end = sub["physical_page_end"]
                    
                    # Assemble raw text from physical page range
                    sub_text = " ".join(p.text for p in book_pages if p_start <= p.page_number <= p_end)
                    
                    print(f"  |- [{idx_sub}/{total_subs}] Subsection {sub_num} '{sub_title}' (pages {p_start}-{p_end}, length {len(sub_text)})")
                    
                    # Generate semantic summary
                    summary = self._summarize_subsection(book_title, ch_title, sub_title, sub_text)
                    
                    node_id = f"{book_id.upper()}-C{ch_index}-S{sub_index}"
                    
                    # Create leaf node
                    sub_node = TreeNode(
                        node_id=node_id,
                        title=f"{sub_num} {sub_title}",
                        summary=summary,
                        book_id=book_id,
                        book_title=book_title,
                        page_start=p_start,
                        page_end=p_end,
                        chapter_title=ch_title,
                        subsection_title=sub_title,
                        children=[],
                    )
                    sub_nodes.append(sub_node)
                    
                    # Create Chunk record for storage
                    all_chunks.append(
                        PageRecord(
                            book_id=book_id,
                            book_title=book_title,
                            page_number=p_start,
                            page_end=p_end,
                            text=sub_text,
                            node_id=node_id,
                            chapter_title=ch_title,
                            subsection_title=sub_title,
                        )
                    )
                    sub_index += 1
                
                # Create Chapter node
                ch_node_id = f"{book_id.upper()}-C{ch_index}"
                ch_node = TreeNode(
                    node_id=ch_node_id,
                    title=ch_title,
                    summary=f"Chapter covering sections: {', '.join(child.title for child in sub_nodes)}",
                    book_id=book_id,
                    book_title=book_title,
                    page_start=min(node.page_start for node in sub_nodes) if sub_nodes else 1,
                    page_end=max(node.page_end for node in sub_nodes) if sub_nodes else 1,
                    chapter_title=ch_title,
                    subsection_title=None,
                    children=sub_nodes,
                )
                chapter_nodes.append(ch_node)
                ch_index += 1

            # Create Book node
            book_node_id = f"{book_id.upper()}-ROOT"
            book_node = TreeNode(
                node_id=book_node_id,
                title=book_title,
                summary=f"Index for the book: {book_title}",
                book_id=book_id,
                book_title=book_title,
                page_start=min(node.page_start for node in chapter_nodes) if chapter_nodes else 1,
                page_end=max(node.page_end for node in chapter_nodes) if chapter_nodes else 1,
                children=chapter_nodes,
            )
            book_nodes.append(book_node)

        # Create combined system root node
        root = TreeNode(
            node_id="ROOT",
            title="Combined Books",
            summary="Root node containing all ingested books.",
            book_id="all",
            book_title="All Books",
            page_start=min(node.page_start for node in book_nodes) if book_nodes else 1,
            page_end=max(node.page_end for node in book_nodes) if book_nodes else 1,
            children=book_nodes,
        )

        return root, all_chunks

    def _extract_toc(self, text: str, fallback_title: str) -> dict[str, Any]:
        system_prompt = (
            "You are an expert PDF Table of Contents (TOC) parser.\n"
            "Your task is to analyze the provided raw text from the first 22 pages of a book and extract its Table of Contents structure.\n"
            "You MUST respond ONLY with the extracted chapters and subsections in the following flat text format. "
            "Do NOT output JSON, do NOT use markdown code blocks, do NOT write intro or outro text.\n\n"
            "Format:\n"
            "Book Title | [Clean Full Book Title]\n"
            "Chapter [Number] | [Chapter Title]\n"
            "[Subsection Number] | [Subsection Title] | [Logical Starting Page Number]\n\n"
            "Example:\n"
            "Book Title | ENERGY EFFICIENCY IN ELECTRICAL UTILITIES\n"
            "Chapter 1 | ELECTRICAL SYSTEMS\n"
            "1.1 | Introduction to Electric Power Supply Systems | 1\n"
            "1.2 | Electricity Billing | 6\n"
            "Chapter 2 | ELECTRICAL MOTORS\n"
            "2.1 | Introduction | 41\n\n"
            "Rules:\n"
            "1. Make sure you extract ALL chapters and subsections (e.g. up to Chapter 10 or 11 if present).\n"
            "2. Extract exactly the logical starting page number for each subsection as listed in the TOC."
        )

        # Truncate text to fit context comfortably
        user_prompt = f"Table of Contents raw text:\n\n{text[:25000]}\n\nExtract and return the structured Table of Contents:"
        
        response = self.llm.generate_text(
            system_prompt,
            user_prompt,
            temperature=self.settings.index_summary_temperature,
        )
        
        return self._parse_flat_toc(response.text, fallback_title)

    def _parse_flat_toc(self, text: str, fallback_title: str) -> dict[str, Any]:
        book_title = fallback_title
        chapters = []
        current_chapter = None
        
        for line in text.splitlines():
            line = line.strip()
            line = line.replace("*", "").replace("#", "").strip()
            if not line:
                continue
                
            if "|" in line:
                parts = line.split("|")
                first_part = parts[0].strip()
                
                if first_part.lower() == "book title" and len(parts) > 1:
                    book_title = parts[1].strip()
                elif "chapter" in first_part.lower() and len(parts) > 1:
                    ch_title = parts[1].strip()
                    ch_num_match = re.search(r"\d+", first_part)
                    ch_num = ch_num_match.group(0) if ch_num_match else ""
                    current_chapter = {
                        "chapter_number": ch_num,
                        "chapter_title": ch_title,
                        "subsections": []
                    }
                    chapters.append(current_chapter)
                elif len(parts) >= 3:
                    sub_num = parts[0].strip()
                    sub_title = parts[1].strip()
                    page_str = parts[2].strip()
                    page_num = self._safe_int(page_str, 1)
                    
                    if current_chapter is None:
                        current_chapter = {
                            "chapter_number": "1",
                            "chapter_title": "General",
                            "subsections": []
                        }
                        chapters.append(current_chapter)
                        
                    current_chapter["subsections"].append({
                        "subsection_number": sub_num,
                        "subsection_title": sub_title,
                        "logical_page_start": page_num
                    })
                    
        return {"book_title": book_title, "chapters": chapters}

    def _summarize_subsection(self, book_title: str, chapter_title: str, sub_title: str, text: str) -> str:
        # Generate summary of subsection text
        system_prompt = (
            "You are a precise assistant generating short summaries for Table of Contents sections. "
            "Create a concise 2-3 sentence summary of the main topics discussed in the text. "
            "Do not include external knowledge, only summarize the provided text."
        )
        user_prompt = (
            f"Book: {book_title}\n"
            f"Chapter: {chapter_title}\n"
            f"Subsection: {sub_title}\n"
            f"Text excerpt (first 8000 characters):\n\n{text[:8000]}\n\n"
            "Provide the 2-3 sentence summary:"
        )
        try:
            response = self.llm.generate_text(
                system_prompt,
                user_prompt,
                temperature=self.settings.index_summary_temperature
            )
            return response.text.strip()
        except Exception as exc:
            print(f"[PageIndexBuilder] Error summarizing subsection '{sub_title}': {exc}")
            return f"Summary of content in subsection: {sub_title}."
