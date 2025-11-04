"""Thin wrapper around the OpenAI client with retry handling."""

from __future__ import annotations

import time
from typing import Dict, List

from openai import OpenAI

from . import config

_CLIENT: OpenAI | None = None


def _get_client() -> OpenAI:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = OpenAI()
    return _CLIENT


def _reasoning_params(settings: config.GPTSettings) -> Dict:
    model_lower = settings.model.lower()
    if settings.reasoning_effort and any(
        token in model_lower for token in ("thinking", "reasoning", "gpt-5")
    ):
        return {"reasoning": {"effort": settings.reasoning_effort}}
    return {}


def fetch_completion(messages: List[Dict[str, str]]) -> str:
    """Send the chat request with retries and return the string content."""

    settings = config.get_settings()
    client = _get_client()

    last_error: Exception | None = None
    for attempt in range(1, settings.retry_attempts + 1):
        try:
            extra_kwargs = {}
            reasoning_body = _reasoning_params(settings)
            if reasoning_body:
                extra_kwargs["extra_body"] = reasoning_body

            response = client.chat.completions.create(
                model=settings.model,
                messages=messages,
                temperature=settings.temperature,
                max_tokens=settings.max_output_tokens,
                response_format={"type": "json_object"},
                **extra_kwargs,
            )
            choice = response.choices[0]
            content = choice.message.content
            if not content or not content.strip():
                raise ValueError("Empty response from model.")
            return content.strip()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt == settings.retry_attempts:
                break
            time.sleep(settings.retry_backoff_seconds * attempt)

    assert last_error is not None  # for mypy
    raise last_error
