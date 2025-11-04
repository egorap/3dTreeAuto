"""Configuration helpers for the GPT parsing pipeline."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(name)
    return value if value not in (None, "") else default


@dataclass(frozen=True)
class GPTSettings:
    """Runtime settings for calling the OpenAI API."""

    model: str = _env("GPT_MODEL", "gpt-4o-mini")  # Fastest option for experimentation.
    temperature: float = float(_env("GPT_TEMPERATURE", "0.2"))
    max_output_tokens: int = int(_env("GPT_MAX_OUTPUT_TOKENS", "600"))
    reasoning_effort: Optional[str] = _env("GPT_REASONING_EFFORT", "low")
    request_timeout: int = int(_env("GPT_TIMEOUT_SECONDS", "30"))
    retry_attempts: int = int(_env("GPT_RETRY_ATTEMPTS", "3"))
    retry_backoff_seconds: float = float(_env("GPT_RETRY_BACKOFF", "2.0"))


def get_settings() -> GPTSettings:
    """Return the active GPT configuration."""

    return GPTSettings()
