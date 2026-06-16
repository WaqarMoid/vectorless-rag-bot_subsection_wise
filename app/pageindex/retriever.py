from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app.config import Settings
from app.llm.mistral_client import MistralClient
from app.models import PageRecord, TreeNode


@dataclass
class RetrievedNode:
    node: TreeNode
    text: str


class TreeRetriever:
    def __init__(self, settings: Settings, llm: MistralClient, root: TreeNode, pages: list[PageRecord]) -> None:
        self.settings = settings
        self.llm = llm
        self.root = root
        self.pages = pages
        # Cache chunks by node_id for O(1) lookup
        self.chunks_by_node_id = {page.node_id: page for page in pages}
        
        # Flatten all leaf nodes (subsections) in the tree
        self.leaf_nodes: dict[str, TreeNode] = {}
        self._collect_leaf_nodes(self.root)

    def _collect_leaf_nodes(self, node: TreeNode) -> None:
        if not node.children:
            if node.node_id != "ROOT" and not node.node_id.endswith("-ROOT") and not node.node_id.endswith("-EMPTY"):
                self.leaf_nodes[node.node_id] = node
        else:
            for child in node.children:
                self._collect_leaf_nodes(child)

    def retrieve(self, question: str, history: list | None = None) -> list[RetrievedNode]:
        if not self.leaf_nodes:
            print("[TreeRetriever] No leaf nodes available in the index.")
            return []

        # Step 1: Format Table of Contents hierarchy for the prompt
        toc_lines = []
        for book_node in self.root.children:
            toc_lines.append(f"Book ID: {book_node.book_id} | Title: {book_node.book_title}")
            for ch_node in book_node.children:
                toc_lines.append(f"  ├─ Chapter: {ch_node.title}")
                for sub_node in ch_node.children:
                    toc_lines.append(
                        f"  │    └─ Subsection ID: {sub_node.node_id} | "
                        f"Title: {sub_node.title} | Summary: {sub_node.summary}"
                    )
        
        toc_hierarchy = "\n".join(toc_lines)

        # Step 1.5: Build conversation history block if available
        history_block = ""
        if history:
            recent = history[-6:]  # last 3 pairs max
            history_lines = []
            for turn in recent:
                role = turn.role if hasattr(turn, 'role') else turn.get('role', '')
                content = turn.content if hasattr(turn, 'content') else turn.get('content', '')
                # Truncate long assistant replies to save tokens
                if role == "assistant" and len(content) > 400:
                    content = content[:400] + "..."
                label = "User" if role == "user" else "Assistant"
                history_lines.append(f"{label}: {content}")
            history_block = (
                "\n\nConversation history (use this to understand what the user is referring to):\n"
                + "\n".join(history_lines)
            )

        # Step 2: Query Mistral to route user query to the most relevant subsections
        system_prompt = (
            "You are a Table of Contents (TOC) routing agent for a Vectorless RAG system. "
            "You are shown the hierarchical structure (Book -> Chapter -> Subsection) of the library. "
            "Your task is to select the most relevant Subsection IDs that are likely to contain the facts "
            "needed to answer the user's question.\n"
            "IMPORTANT: If the user's message is a follow-up (e.g. 'explain in detail', 'tell me more', "
            "'what about X?'), use the conversation history to understand what topic they are referring to, "
            "and select subsections relevant to THAT topic.\n"
            "Rules:\n"
            f"1. Select at most {self.settings.max_context_subsections} Subsection IDs.\n"
            "2. If the question is outside the scope of the books (cannot be answered by any of the sections), "
            "set 'insufficient': true.\n"
            "3. You MUST respond with a valid JSON object matching this schema:\n"
            "{\n"
            "  \"selected_ids\": [\"NODE_ID1\", \"NODE_ID2\"],\n"
            "  \"insufficient\": false\n"
            "}\n"
            "4. Mention 'JSON' in your response and wrap the output in a JSON code block."
        )

        user_prompt = (
            f"Question:\n{question}\n"
            f"{history_block}\n\n"
            f"Library Table of Contents Hierarchy:\n\n{toc_hierarchy}\n\n"
            "Select the most relevant Subsection IDs:"
        )

        try:
            response = self.llm.generate_text(
                system_prompt,
                user_prompt,
                temperature=self.settings.retrieval_temperature,
                response_format={"type": "json_object"}
            )
            payload = self._parse_json(response.text)
        except Exception as exc:
            print(f"[TreeRetriever] LLM retrieval failed: {exc}")
            return []

        if not payload or payload.get("insufficient") is True:
            return []

        selected_ids = [str(item).strip() for item in payload.get("selected_ids", []) if item]
        selected_ids = selected_ids[:self.settings.max_context_subsections]
        
        results: list[RetrievedNode] = []
        for node_id in selected_ids:
            # Look up the TreeNode metadata
            node = self.leaf_nodes.get(node_id)
            # Look up the raw text of the chunk
            chunk = self.chunks_by_node_id.get(node_id)
            
            if node and chunk:
                results.append(RetrievedNode(node=node, text=chunk.text))
            elif node_id in self.leaf_nodes:
                # If chunk text is missing but node exists
                results.append(RetrievedNode(node=self.leaf_nodes[node_id], text=""))

        # Limit character count in final retrieved contexts
        total_chars = 0
        final_results = []
        for res in results:
            if total_chars + len(res.text) > self.settings.max_context_chars:
                allowed_len = max(0, self.settings.max_context_chars - total_chars)
                if allowed_len > 100:
                    res.text = res.text[:allowed_len] + "..."
                    final_results.append(res)
                break
            final_results.append(res)
            total_chars += len(res.text)

        return final_results

    def _parse_json(self, raw: str) -> dict | None:
        cleaned = raw.strip()
        cleaned = cleaned.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            payload = json.loads(cleaned)
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
            if match:
                try:
                    payload = json.loads(match.group(0))
                    if isinstance(payload, dict):
                        return payload
                except json.JSONDecodeError:
                    pass
        return None
