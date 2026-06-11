from __future__ import annotations

import json
import time
import random
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import Settings


@dataclass
class MistralResponse:
    text: str


class MistralClient:
    def __init__(self, settings: Settings) -> None:
        if not settings.mistral_api_key:
            raise ValueError("MISTRAL_API_KEY environment variable is required.")
        self.api_key = settings.mistral_api_key
        self.model = settings.mistral_model

    def generate_text(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        max_output_tokens: int | None = None,
        response_format: dict[str, str] | None = None,
    ) -> MistralResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
        }
        if max_output_tokens is not None:
            payload["max_tokens"] = max_output_tokens
        if response_format is not None:
            payload["response_format"] = response_format

        url = "https://api.mistral.ai/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        request_data = json.dumps(payload).encode("utf-8")
        
        max_retries = 6
        base_delay = 1.0  # seconds
        
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
                    text = data["choices"][0]["message"]["content"].strip()
                    return MistralResponse(text=text)
            except HTTPError as exc:
                if exc.code in (429, 500, 503) and attempt < max_retries - 1:
                    sleep_time = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
                    print(f"[MistralClient] HTTP {exc.code} received. Retrying in {sleep_time:.2f}s... (Attempt {attempt + 1}/{max_retries})")
                    time.sleep(sleep_time)
                    continue
                raise RuntimeError(f"Mistral API HTTP error: {exc.code} - {exc.reason}") from exc
            except URLError as exc:
                if attempt < max_retries - 1:
                    sleep_time = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
                    print(f"[MistralClient] Connection failed. Retrying in {sleep_time:.2f}s... (Attempt {attempt + 1}/{max_retries})")
                    time.sleep(sleep_time)
                    continue
                raise RuntimeError("Mistral API connection failed.") from exc
                
        raise RuntimeError("Mistral API request failed after maximum retries.")
