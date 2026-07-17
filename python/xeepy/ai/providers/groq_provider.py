"""
Groq Provider

Fast, free AI comment generation using Groq's inference API.
Free tier: 14,400 requests/day per model, separate token quota per model.

Get a free API key at: https://console.groq.com
Set: GROQ_API_KEY=gsk_... in your .env file.

Fallback chain (each model has its own daily token quota):
  llama-3.3-70b-versatile  — best quality (~100k TPD)
  llama-3.1-8b-instant     — fastest, higher limit (~500k TPD)
  gemma2-9b-it             — alternative, good quality (~500k TPD)
"""

from __future__ import annotations

import os
from typing import Optional

from loguru import logger

try:
    from groq import AsyncGroq, RateLimitError
    _GROQ_AVAILABLE = True
except ImportError:
    _GROQ_AVAILABLE = False
    RateLimitError = Exception  # type: ignore


class GroqProvider:
    """
    Generate text via Groq's API with automatic model fallback on rate limits.
    """

    DEFAULT_MODEL = "llama-3.3-70b-versatile"

    FALLBACK_MODELS = [
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "gemma2-9b-it",
    ]

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout_s: int = 30,
    ):
        if not _GROQ_AVAILABLE:
            raise ImportError("groq not installed. Run: pip install groq")

        self._api_key = api_key or os.environ.get("GROQ_API_KEY")
        if not self._api_key:
            raise ValueError(
                "GROQ_API_KEY not set. Get a free key at https://console.groq.com "
                "and add GROQ_API_KEY=gsk_... to your .env file."
            )
        # If a custom model is set, put it first in the chain then append the rest
        primary = model or self.DEFAULT_MODEL
        rest = [m for m in self.FALLBACK_MODELS if m != primary]
        self._models = [primary] + rest

        self._timeout = timeout_s
        self._client: AsyncGroq | None = None

    async def start(self) -> None:
        self._client = AsyncGroq(api_key=self._api_key)
        logger.info(f"Groq provider ready (primary: {self._models[0]}, fallbacks: {self._models[1:]})")

    async def stop(self) -> None:
        self._client = None

    async def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        *,
        json_mode: bool = False,
        max_tokens: int = 80,
    ) -> Optional[str]:
        """
        Generate a short reply. Tries each model in the fallback chain on 429.
        Returns None if all models are exhausted.

        json_mode=True asks the model for a strict JSON object (used so the
        caller gets an explicit relevant/comment decision instead of having to
        parse free-form prose).
        """
        if not self._client:
            await self.start()

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        extra = {"response_format": {"type": "json_object"}} if json_mode else {}

        for model in self._models:
            try:
                resp = await self._client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=0.9,
                    timeout=self._timeout,
                    **extra,
                )
                text = resp.choices[0].message.content
                if model != self._models[0]:
                    logger.debug(f"Groq fallback model used: {model}")
                return text.strip() if text else None
            except RateLimitError:
                logger.warning(f"Groq rate limit hit on {model} — trying next fallback")
                continue
            except Exception as e:
                logger.error(f"Groq error on {model}: {e}")
                return None

        logger.error("Groq: all models exhausted (rate limited). Skipping comment.")
        return None
