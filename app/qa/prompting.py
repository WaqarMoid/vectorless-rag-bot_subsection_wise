from __future__ import annotations

from app.pageindex.retriever import RetrievedNode


def build_messages(
    question: str,
    nodes: list[RetrievedNode],
    out_of_bounds_text: str,
    history: list | None = None,
) -> tuple[str, str]:
    context_blocks: list[str] = []
    for item in nodes:
        node = item.node
        context_blocks.append(
            f"[{node.node_id}] "
            f"book_id={node.book_id}; "
            f"book_title={node.book_title}; "
            f"chapter={node.chapter_title}; "
            f"subsection={node.subsection_title}; "
            f"pages={node.page_start}-{node.page_end}\n"
            f"{item.text}"
        )

    system = (
        "You are a strict retrieval-grounded assistant.\n"
        "You are allowed to answer ONLY using facts present in the provided context.\n"
        f'If the context is insufficient or the question is outside the books, respond exactly: "{out_of_bounds_text}"\n'
        "Never use outside knowledge.\n"
        "Output must be valid JSON with this schema:\n"
        "{\n"
        "  \"answer\": \"your detailed answer string\",\n"
        "  \"citation_ids\": [\"NODE_ID\"],\n"
        "  \"grounded\": true/false\n"
        "}\n"
        "Rules:\n"
        "1) For any supported answer, set grounded=true and include the citation_ids (e.g. \"BOOK_1-C1-S1\") for every section used.\n"
        f"2) For unsupported/out-of-bounds questions, return exactly:\n"
        f"   {{\"answer\": \"{out_of_bounds_text}\", \"citation_ids\": [], \"grounded\": false}}\n"
        "3) Mention 'JSON' in your response and wrap the output in a JSON code block."
    )

    # Build conversation history block if available
    history_block = ""
    if history:
        recent = history[-6:]  # last 3 pairs max
        history_lines = []
        for turn in recent:
            role = turn.role if hasattr(turn, 'role') else turn.get('role', '')
            content = turn.content if hasattr(turn, 'content') else turn.get('content', '')
            # Truncate long assistant replies
            if role == "assistant" and len(content) > 500:
                content = content[:500] + "..."
            label = "User" if role == "user" else "Assistant"
            history_lines.append(f"{label}: {content}")
        history_block = "Conversation history:\n" + "\n".join(history_lines) + "\n\n"

    user = (
        f"{history_block}"
        f"Question:\n{question}\n\n"
        f"Context:\n\n{'\n\n'.join(context_blocks)}"
    )
    return system, user
