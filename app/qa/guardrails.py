from __future__ import annotations

from app.config import Settings
from app.pageindex.retriever import RetrievedNode


class Guardrails:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def should_refuse_before_generation(self, nodes: list[RetrievedNode]) -> bool:
        # Refuse if no contexts retrieved
        return not nodes

    def should_refuse_after_generation(
        self,
        answer: str,
        citation_ids: list[str],
        available_citations: set[str],
    ) -> bool:
        cleaned = answer.strip()
        if not cleaned:
            return True
        if cleaned == self.settings.out_of_bounds_text:
            return True
        if not citation_ids:
            return True
        # Ensure model didn't hallucinate citations not in the retrieved context
        if any(cid not in available_citations for cid in citation_ids):
            return True
        return False
