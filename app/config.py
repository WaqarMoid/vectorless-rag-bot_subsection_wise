from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    project_root: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[1])
    data_dir: Path = Field(default_factory=lambda: Path.cwd() / "data")
    index_dir_name: str = "index"

    mistral_api_key: str | None = Field(default=None, validation_alias="MISTRAL_API_KEY")
    mistral_model: str = Field(default="mistral-large-latest", validation_alias="MISTRAL_MODEL")

    max_context_subsections: int = 3
    max_context_chars: int = 16000

    index_summary_temperature: float = 0.2
    retrieval_temperature: float = 0.0
    answer_temperature: float = 0.0

    out_of_bounds_text: str = "Out of bounds for me."

    @property
    def index_dir(self) -> Path:
        return self.data_dir / self.index_dir_name

    @property
    def tree_path(self) -> Path:
        return self.index_dir / "tree.json"

    @property
    def pages_path(self) -> Path:
        return self.index_dir / "pages.jsonl"

    @property
    def manifest_path(self) -> Path:
        return self.index_dir / "manifest.json"

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.index_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
