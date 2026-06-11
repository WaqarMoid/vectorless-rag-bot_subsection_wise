from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class PageRecord:
    book_id: str
    book_title: str
    page_number: int  # physical start page in PDF
    page_end: int     # physical end page in PDF
    text: str
    node_id: str
    chapter_title: str
    subsection_title: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TreeNode:
    node_id: str
    title: str
    summary: str
    book_id: str
    book_title: str
    page_start: int
    page_end: int
    chapter_title: str | None = None
    subsection_title: str | None = None
    children: list["TreeNode"] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "title": self.title,
            "summary": self.summary,
            "book_id": self.book_id,
            "book_title": self.book_title,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "chapter_title": self.chapter_title,
            "subsection_title": self.subsection_title,
            "children": [child.to_dict() for child in self.children],
        }

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> "TreeNode":
        children = [TreeNode.from_dict(item) for item in payload.get("children", [])]
        return TreeNode(
            node_id=payload["node_id"],
            title=payload["title"],
            summary=payload["summary"],
            book_id=payload["book_id"],
            book_title=payload["book_title"],
            page_start=int(payload["page_start"]),
            page_end=int(payload["page_end"]),
            chapter_title=payload.get("chapter_title"),
            subsection_title=payload.get("subsection_title"),
            children=children,
        )


@dataclass
class TreeIndex:
    root: TreeNode
    pages: list[PageRecord]


@dataclass
class Citation:
    citation_id: str
    node_id: str
    book_id: str
    book_title: str
    page_start: int
    page_end: int


@dataclass
class ChatResult:
    answer: str
    out_of_bounds: bool
    citations: list[Citation]
