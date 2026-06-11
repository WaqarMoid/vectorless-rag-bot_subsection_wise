from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.ingest.pipeline import ingest_two_books


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest exactly two book PDFs into the TOC-based PageIndex.")
    parser.add_argument(
        "--books",
        nargs=2,
        required=True,
        help="Paths for the two book PDFs.",
    )
    parser.add_argument(
        "--book-ids",
        nargs=2,
        default=None,
        help="Optional custom IDs for the two books (example: electrical_book thermal_book).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = get_settings()
    pdf_paths = [Path(path) for path in args.books]
    
    print("[Ingestion CLI] Ingesting books and parsing Table of Contents...")
    stats = ingest_two_books(settings=settings, pdf_paths=pdf_paths, book_ids=args.book_ids)

    print("\n--- Ingestion Successful ---")
    print(f"Books indexed: {stats['books_indexed']}")
    print(f"Pages extracted: {stats['pages_extracted']}")
    print(f"Subsection chunks stored: {stats['chunks_stored']}")
    print(f"Total index tree nodes: {stats['total_nodes']}")
    print(f"PageIndex files stored in: {stats['index_location']}")


if __name__ == "__main__":
    main()
