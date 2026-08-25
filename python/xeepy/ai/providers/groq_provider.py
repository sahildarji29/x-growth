"""
Groq Provider

Fast, free AI comment generation using Groq's inference API.
Free tier: 14,400 requests/day per model, separate token quota per model.

Get a free API key at: https://console.groq.com
Set: GROQ_API_KEY=gsk_... in your .env file.

Fallback chain (each model has its own daily token quota; verified available
August 2026 — Groq decommissioned the older Llama/Gemma models, check
https://console.groq.com/docs/models when these 404):
  openai/gpt-oss-120b  — best quality
  openai/gpt-oss-20b   — smaller/faster
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

    DEFAULT_MODEL = "openai/gpt-oss-120b"

    FALLBACK_MODELS = [
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
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
        primary = model or os.environ.get("GROQ_MODEL") or self.DEFAULT_MODEL
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
                kwargs = dict(extra)
                if model.startswith("openai/gpt-oss"):
                    # Reasoning models: keep hidden reasoning short so the small
                    # max_tokens budget is spent on the visible reply.
                    kwargs["extra_body"] = {"reasoning_effort": "low"}
                resp = await self._client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=0.9,
                    timeout=self._timeout,
                    **kwargs,
                )
                text = resp.choices[0].message.content
                if not text or not text.strip():
                    # Reasoning models can spend the whole budget thinking and
                    # return empty content — treat like a failure, not a reply.
                    logger.warning(f"Groq empty response from {model} — trying next fallback")
                    continue
                fallback = " (fallback)" if model != self._models[0] else ""
                logger.info(f"✅ LLM reply via groq / {model}{fallback}")
                return text.strip()
            except RateLimitError:
                logger.warning(f"Groq rate limit hit on {model} — trying next fallback")
                continue
            except Exception as e:
                # e.g. 404 model_not_found when Groq decommissions a model —
                # never let one dead model kill the whole chain.
                logger.warning(f"Groq error on {model}: {e} — trying next fallback")
                continue

        logger.error("Groq: all models exhausted (rate limited or errored). Skipping comment.")
        return None
