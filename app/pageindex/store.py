from __future__ import annotations

import json
from pathlib import Path

from app.config import Settings
from app.models import PageRecord, TreeIndex, TreeNode


class PageIndexStore:
    def __init__(self, tree: TreeNode, pages: list[PageRecord]) -> None:
        self.tree = tree
        self.pages = pages

    @classmethod
    def build(cls, tree: TreeNode, pages: list[PageRecord]) -> "PageIndexStore":
        return cls(tree=tree, pages=pages)

    def save(self, settings: Settings, source_pdfs: list[Path]) -> None:
        settings.ensure_directories()
        
        # Save tree index
        with settings.tree_path.open("w", encoding="utf-8") as f:
            json.dump(self.tree.to_dict(), f, indent=2)

        # Save pages metadata/text
        with settings.pages_path.open("w", encoding="utf-8") as f:
            for page in self.pages:
                f.write(json.dumps(page.to_dict(), ensure_ascii=False) + "\n")

        # Save manifest
        manifest = {
            "books": [str(path) for path in source_pdfs],
            "pages": len(self.pages),
            "tree_root": self.tree.node_id,
        }
        with settings.manifest_path.open("w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

    @classmethod
    def load(cls, settings: Settings) -> TreeIndex:
        if not settings.tree_path.exists() or not settings.pages_path.exists():
            raise FileNotFoundError(
                f"Index files not found in {settings.index_dir}. Ingest book PDFs first."
            )

        with settings.tree_path.open("r", encoding="utf-8") as f:
            tree = TreeNode.from_dict(json.load(f))

        pages: list[PageRecord] = []
        with settings.pages_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                pages.append(PageRecord(**payload))

        return TreeIndex(root=tree, pages=pages)
