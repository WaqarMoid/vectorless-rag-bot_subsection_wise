from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import Settings


@dataclass
class GeminiResponse:
    text: str


DEFAULT_GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-flash-lite-latest",
    "gemini-flash-latest",
    "gemini-3.5-flash-lite",
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
]


class GeminiClient:
    def __init__(self, settings: Settings) -> None:
        if not settings.gemini_api_key or settings.gemini_api_key == "your_gemini_api_key_here":
            raise ValueError("GEMINI_API_KEY environment variable is required.")
        self.api_key = settings.gemini_api_key
        self.preferred_model = (settings.gemini_model or "").strip() or None
        self._active_model: str | None = None

    def _candidate_models(self) -> list[str]:
        ordered: list[str] = []
        if self._active_model:
            ordered.append(self._active_model)
        for model in DEFAULT_GEMINI_MODELS:
            if model not in ordered:
                ordered.append(model)
        if self.preferred_model and self.preferred_model not in ordered:
            ordered.append(self.preferred_model)
        return ordered

    def _discover_available_models(self, already_tried: set[str]) -> list[str]:
        """Query Gemini /v1beta/models for any remaining generateContent text models."""
        req = Request(
            "https://generativelanguage.googleapis.com/v1beta/models",
            headers={"Accept": "application/json", "x-goog-api-key": self.api_key},
            method="GET",
        )
        try:
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return []

        discovered: list[str] = []
        for entry in data.get("models") or []:
            name = str(entry.get("name", "")).removeprefix("models/")
            methods = entry.get("supportedGenerationMethods") or []
            lower = name.lower()
            if (
                not name
                or name in already_tried
                or "generateContent" not in methods
                or "flash" not in lower
                or any(
                    skip in lower
                    for skip in ("preview", "tts", "image", "embed", "transcribe", "realtime", "audio")
                )
            ):
                continue
            discovered.append(name)
        return discovered

    def _call_model(self, model: str, request_data: bytes) -> GeminiResponse:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "x-goog-api-key": self.api_key,
        }
        request = Request(url, data=request_data, headers=headers, method="POST")
        with urlopen(request, timeout=20) as response:
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
        request_data = json.dumps(payload).encode("utf-8")

        models_to_try = self._candidate_models()
        tried: set[str] = set()
        last_error: str = "Unknown error"

        for round_idx in range(2):
            for model in models_to_try:
                tried.add(model)
                try:
                    result = self._call_model(model, request_data)
                    self._active_model = model
                    return result
                except HTTPError as exc:
                    error_body = exc.read().decode("utf-8", errors="replace")
                    detail = error_body[:300] if error_body else exc.reason
                    last_error = f"HTTP {exc.code} ({model}) - {detail}"
                    if exc.code in (400, 401, 403):
                        # Auth/key errors won't be fixed by switching models unless model-specific 403
                        if exc.code == 401 or "api key" in detail.lower():
                            raise RuntimeError(f"Gemini API HTTP error: {last_error}") from exc
                    print(f"[GeminiClient] Model '{model}' failed ({exc.code}); trying next available model...")
                    if self._active_model == model:
                        self._active_model = None
                    continue
                except (URLError, TimeoutError, OSError) as exc:
                    last_error = f"{type(exc).__name__} ({model}): {exc}"
                    print(f"[GeminiClient] Model '{model}' network/timeout error; trying next available model...")
                    if self._active_model == model:
                        self._active_model = None
                    continue

            if round_idx == 0:
                extra = self._discover_available_models(tried)
                if extra:
                    models_to_try = extra
                    continue
                time.sleep(1.0)
                models_to_try = self._candidate_models()

        raise RuntimeError(f"Gemini API failed across available models. Last error: {last_error}")
