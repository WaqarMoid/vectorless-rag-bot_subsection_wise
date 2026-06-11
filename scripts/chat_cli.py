from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.qa.engine import ChatEngine


def main() -> None:
    settings = get_settings()
    
    print("[CLI Chat] Initializing Chat Engine (loading PageIndex)...")
    try:
        engine = ChatEngine.from_settings(settings)
        print("[CLI Chat] Ready! Type your question or 'exit' to quit.\n")
    except Exception as exc:
        print(f"[CLI Chat] Initialization failed: {exc}")
        print("Please ingest books first using: python scripts/ingest_books.py --books <path1> <path2>")
        sys.exit(1)

    while True:
        try:
            query = input("Ask: ").strip()
            if not query:
                continue
            if query.lower() in ("exit", "quit"):
                break
                
            print("[...] Searching index and generating answer...")
            result = engine.ask(query)
            
            print(f"\nAnswer: {result.answer}")
            if result.citations:
                print("Citations:")
                for c in result.citations:
                    print(f" - [{c.citation_id}] {c.book_title} (pages {c.page_start}-{c.page_end})")
            print()
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye!")
            break


if __name__ == "__main__":
    main()
