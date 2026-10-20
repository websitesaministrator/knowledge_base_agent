"""
Shared LLM client wrapper.
Keeps the model swappable via env vars; adds retry/backoff for all calls.
"""
from __future__ import annotations
import json
import logging
from typing import Any

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential, before_sleep_log

from config import settings

logger = logging.getLogger(__name__)

# Single shared client — thread-safe for FastAPI
_client: OpenAI | None = None


def get_client() -> OpenAI:
    """Return (or create) the OpenAI client singleton."""
    global _client
    if _client is None:
        _client = OpenAI(api_key=settings.openai_api_key)
    return _client


@retry(
    stop=stop_after_attempt(settings.agent_retry_attempts),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def chat_completion(
    messages: list[dict],
    model: str | None = None,
    response_format: dict | None = None,
    temperature: float = 0.2,
    max_tokens: int = 2048,
) -> str:
    """
    Call the LLM with retry/backoff.
    Returns the raw text content of the first choice.
    Uses JSON mode when response_format={'type':'json_object'} is passed.
    """
    client = get_client()
    kwargs: dict[str, Any] = {
        "model": model or settings.llm_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        kwargs["response_format"] = response_format

    resp = client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content or ""


def chat_json(messages: list[dict], **kwargs) -> dict:
    """
    Call LLM in JSON mode and parse the result.
    Raises ValueError if the response is not valid JSON.
    """
    raw = chat_completion(
        messages,
        response_format={"type": "json_object"},
        **kwargs,
    )
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("LLM returned invalid JSON: %s", raw[:500])
        raise ValueError(f"LLM returned invalid JSON: {e}") from e
