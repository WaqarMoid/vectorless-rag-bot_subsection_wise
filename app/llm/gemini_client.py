from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import Settings


@dataclass
class GeminiResponse:
    text: str


class GeminiClient:
    def __init__(self, settings: Settings) -> None:
        if not settings.gemini_api_key or settings.gemini_api_key == "your_gemini_api_key_here":
            raise ValueError("GEMINI_API_KEY environment variable is required.")
        if "flash-lite" in settings.gemini_model.lower():
            raise ValueError(
                f"Model '{settings.gemini_model}' is not allowed. Use Gemini 3.8 Flash or above (no flash-lite)."
            )
        self.api_key = settings.gemini_api_key
        self.model = settings.gemini_model

    def generate_text(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        max_output_tokens: int | None = None,
        response_format: dict[str, str] | None = None,
    ) -> GeminiResponse:
        generation_config: dict[str, Any] = {
            "temperature": temperature,
        }
        if max_output_tokens is not None:
            generation_config["maxOutputTokens"] = max_output_tokens
        if response_format and response_format.get("type") == "json_object":
            generation_config["responseMimeType"] = "application/json"

        payload: dict[str, Any] = {
            "systemInstruction": {
                "parts": [{"text": system_prompt}],
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": user_prompt}],
                }
            ],
            "generationConfig": generation_config,
        }

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "x-goog-api-key": self.api_key,
        }

        request_data = json.dumps(payload).encode("utf-8")

        max_retries = 4
        base_delay = 1.0  # seconds (keep within Vercel serverless timeout budget)

        for attempt in range(max_retries):
            request = Request(
                url,
                data=request_data,
                headers=headers,
                method="POST",
            )
            try:
                with urlopen(request, timeout=120) as response:
                    data = json.loads(response.read().decode("utf-8"))
                    candidates = data.get("candidates") or []
                    if not candidates:
                        block_reason = (data.get("promptFeedback") or {}).get("blockReason", "unknown")
                        raise RuntimeError(f"Gemini API returned no candidates (blockReason={block_reason}).")
                    parts = (candidates[0].get("content") or {}).get("parts") or []
                    text_parts = [
                        part["text"]
                        for part in parts
                        if isinstance(part, dict) and not part.get("thought") and "text" in part
                    ]
                    text = "\n".join(text_parts).strip()
                    if not text:
                        finish_reason = candidates[0].get("finishReason", "unknown")
                        raise RuntimeError(f"Gemini API returned empty content (finishReason={finish_reason}).")
                    return GeminiResponse(text=text)
            except HTTPError as exc:
                error_body = exc.read().decode("utf-8", errors="replace")
                if exc.code in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                    sleep_time = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
                    print(
                        f"[GeminiClient] HTTP {exc.code} received. Retrying in {sleep_time:.2f}s... "
                        f"(Attempt {attempt + 1}/{max_retries})"
                    )
                    time.sleep(sleep_time)
                    continue
                detail = error_body[:400] if error_body else exc.reason
                raise RuntimeError(f"Gemini API HTTP error: {exc.code} - {detail}") from exc
            except URLError as exc:
                if attempt < max_retries - 1:
                    sleep_time = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
                    print(
                        f"[GeminiClient] Connection failed. Retrying in {sleep_time:.2f}s... "
                        f"(Attempt {attempt + 1}/{max_retries})"
                    )
                    time.sleep(sleep_time)
                    continue
                raise RuntimeError("Gemini API connection failed.") from exc

        raise RuntimeError("Gemini API request failed after maximum retries.")
