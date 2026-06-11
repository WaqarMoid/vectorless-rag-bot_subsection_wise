from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.ingest.pdf_loader import PDFLoader
from app.llm.mistral_client import MistralClient
from app.models import PageRecord
from app.pageindex.indexer import PageIndexBuilder
from app.pageindex.store import PageIndexStore


def ingest_two_books(
    settings: Settings,
    pdf_paths: list[Path],
    book_ids: list[str] | None = None,
) -> dict[str, int | str]:
    if len(pdf_paths) != 2:
        raise ValueError("Exactly two PDF files are required.")

    resolved = [path.expanduser().resolve() for path in pdf_paths]
    for path in resolved:
        if not path.exists():
            raise FileNotFoundError(f"PDF not found: {path}")
        if path.suffix.lower() != ".pdf":
            raise ValueError(f"Not a PDF file: {path}")

    ids = book_ids or [f"book_{i + 1}" for i in range(2)]
    if len(ids) != 2:
        raise ValueError("Exactly two book IDs are required when provided.")

    settings.ensure_directories()
    loader = PDFLoader()

    # Step 1: Load raw pages
    all_pages: list[PageRecord] = []
    for path, book_id in zip(resolved, ids):
        pages = loader.load(path, book_id=book_id)
        if not pages:
            raise ValueError(
                f"No extractable text found in {path}. If this is a scanned PDF, "
                "install OCR dependencies (Tesseract + Poppler) and retry."
            )
        all_pages.extend(pages)

    # Step 2: Build TOC tree and chunks
    llm = MistralClient(settings)
    builder = PageIndexBuilder(settings, llm)
    tree, chunks = builder.build_tree(all_pages)

    # Step 3: Save to PageIndexStore
    store = PageIndexStore.build(tree=tree, pages=chunks)
    store.save(settings, source_pdfs=resolved)
    node_count = _count_nodes(tree)

    return {
        "books_indexed": len(resolved),
        "pages_extracted": len(all_pages),
        "chunks_stored": len(chunks),
        "total_nodes": node_count,
        "index_location": str(settings.index_dir),
    }


def _count_nodes(node: object) -> int:
    if not hasattr(node, "children"):
        return 1
    children = getattr(node, "children") or []
    return 1 + sum(_count_nodes(child) for child in children)
