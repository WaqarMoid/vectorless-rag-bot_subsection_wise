from __future__ import annotations

import json
import re
from typing import Any

from app.config import Settings, get_settings
from app.llm.mistral_client import MistralClient
from app.models import ChatResult, Citation
from app.pageindex.retriever import TreeRetriever
from app.pageindex.store import PageIndexStore
from app.qa.guardrails import Guardrails
from app.qa.prompting import build_messages


class ChatEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.llm = MistralClient(settings)
        self.index = PageIndexStore.load(settings)
        self.retriever = TreeRetriever(
            settings=settings,
            llm=self.llm,
            root=self.index.root,
            pages=self.index.pages,
        )
        self.guardrails = Guardrails(settings)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "ChatEngine":
        return cls(settings or get_settings())

    def ask(self, question: str, history: list | None = None) -> ChatResult:
        query = question.strip()
        if not query:
            return self._out_of_bounds()

        # Step 0: Rewrite follow-up queries using conversation history
        retrieval_query = self._rewrite_query(query, history or [])

        # Step 1: Semantic routing (use rewritten query for better retrieval)
        context = self.retriever.retrieve(retrieval_query)

        # Step 2: Pre-generation guardrails
        if self.guardrails.should_refuse_before_generation(context):
            return self._out_of_bounds()

        # Step 3: Build prompts and call Mistral LLM
        # Use the ORIGINAL query + history for answer generation (gives full conversational context)
        system_prompt, user_prompt = build_messages(
            question=query,
            nodes=context,
            out_of_bounds_text=self.settings.out_of_bounds_text,
            history=history or [],
        )
        
        try:
            raw_response = self.llm.generate_text(
                system_prompt,
                user_prompt,
                temperature=self.settings.answer_temperature,
                response_format={"type": "json_object"}
            ).text
            parsed = self._parse_model_json(raw_response)
        except Exception as exc:
            print(f"[ChatEngine] LLM generation failed: {exc}")
            return self._out_of_bounds()

        if parsed is None:
            return self._out_of_bounds()

        answer = str(parsed.get("answer", "")).strip()
        grounded = bool(parsed.get("grounded", False))
        citation_ids = self._normalize_citation_ids(parsed.get("citation_ids", []))
        available_citation_ids = {item.node.node_id for item in context}

        # Step 4: Post-generation guardrails
        if not grounded:
            return self._out_of_bounds()
        if self.guardrails.should_refuse_after_generation(answer, citation_ids, available_citation_ids):
            return self._out_of_bounds()

        # Step 5: Format citations
        citations_by_id = {item.node.node_id: item.node for item in context}
        citations: list[Citation] = []
        seen: set[str] = set()
        for citation_id in citation_ids:
            if citation_id in seen:
                continue
            chunk = citations_by_id[citation_id]
            citations.append(
                Citation(
                    citation_id=citation_id,
                    node_id=chunk.node_id,
                    book_id=chunk.book_id,
                    book_title=chunk.book_title,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                )
            )
            seen.add(citation_id)

        return ChatResult(answer=answer, out_of_bounds=False, citations=citations)

    def _out_of_bounds(self) -> ChatResult:
        return ChatResult(
            answer=self.settings.out_of_bounds_text,
            out_of_bounds=True,
            citations=[],
        )

    def _normalize_citation_ids(self, raw: Any) -> list[str]:
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            return []
        normalized: list[str] = []
        for item in raw:
            token = str(item).strip().upper()
            if re.fullmatch(r"[A-Z0-9\-_]+", token):
                normalized.append(token)
        return normalized

    def _parse_model_json(self, raw_output: str) -> dict[str, Any] | None:
        raw = raw_output.strip()
        if not raw:
            return None

        # Unwrap markdown fences
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

        try:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass

        # Regex search fallback
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            return None
        try:
            payload = json.loads(match.group(0))
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            return None
        return None

    def _rewrite_query(self, current_query: str, history: list) -> str:
        """Rewrite an ambiguous follow-up query into a standalone question
        using recent conversation history. If history is empty or the query
        already looks self-contained, return it as-is."""
        if not history:
            return current_query

        # Only use the last few turns to keep the prompt small
        recent = history[-6:]  # last 3 pairs max

        # Quick heuristic: if the query is long enough and contains
        # domain-relevant keywords, skip the rewrite to save an API call
        domain_keywords = [
            "motor", "compressor", "boiler", "transformer", "hvac", "pump",
            "fan", "blower", "steam", "heat", "energy", "power", "voltage",
            "combustion", "fuel", "insulation", "efficiency", "refrigeration",
            "lighting", "vfd", "vsd", "bee", "electrical", "thermal",
        ]
        lower_q = current_query.lower()
        word_count = len(current_query.split())
        has_keyword = any(kw in lower_q for kw in domain_keywords)
        if word_count >= 8 and has_keyword:
            return current_query

        # Build a compact conversation snippet
        convo_lines = []
        for turn in recent:
            role = turn.role if hasattr(turn, 'role') else turn.get('role', '')
            content = turn.content if hasattr(turn, 'content') else turn.get('content', '')
            # Truncate long assistant replies to save tokens
            if role == "assistant" and len(content) > 300:
                content = content[:300] + "..."
            convo_lines.append(f"{role.upper()}: {content}")

        convo_text = "\n".join(convo_lines)

        system_prompt = (
            "You are a query rewriter. Given a conversation history and the user's latest message, "
            "rewrite the latest message into a single, fully self-contained question that can be "
            "understood WITHOUT any conversation history. "
            "The rewritten query must preserve the user's original intent exactly. "
            "If the latest message is already self-contained, return it unchanged. "
            "Reply with ONLY the rewritten question, nothing else."
        )
        user_prompt = (
            f"Conversation history:\n{convo_text}\n\n"
            f"Latest user message: {current_query}\n\n"
            f"Rewritten standalone question:"
        )

        try:
            response = self.llm.generate_text(
                system_prompt,
                user_prompt,
                temperature=0.0,
                max_output_tokens=200,
            )
            rewritten = response.text.strip().strip('"').strip()
            if rewritten:
                print(f"[ChatEngine] Query rewritten: '{current_query}' -> '{rewritten}'")
                return rewritten
        except Exception as exc:
            print(f"[ChatEngine] Query rewrite failed, using original: {exc}")

        return current_query
