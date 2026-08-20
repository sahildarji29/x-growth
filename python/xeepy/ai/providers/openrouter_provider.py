"""
OpenRouter Provider

AI comment generation via OpenRouter's OpenAI-compatible API.
One key, many models (including free-tier models).

Get an API key at: https://openrouter.ai/keys
Set in your .env file:
  LLM_PROVIDER=openrouter
  OPENROUTER_API_KEY=sk-or-...
  OPENROUTER_MODEL=meta-llama/llama-3.3-70b-instruct:free   # optional

Fallback chain on rate limits (429). Model IDs change over time — check
https://openrouter.ai/models for what's currently available.
"""

from __future__ import annotations

import os
from typing import Optional

import httpx
from loguru import logger


class OpenRouterProvider:
    """
    Generate text via OpenRouter's chat completions API with automatic
    model fallback on rate limits. Same interface as GroqProvider.
    """

    API_URL = "https://openrouter.ai/api/v1/chat/completions"

    DEFAULT_MODEL = "meta-llama/llama-3.3-70b-instruct:free"

    FALLBACK_MODELS = [
        "meta-llama/llama-3.3-70b-instruct:free",
        "google/gemma-2-9b-it:free",
        "mistralai/mistral-7b-instruct:free",
    ]

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout_s: int = 30,
    ):
        self._api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if not self._api_key:
            raise ValueError(
                "OPENROUTER_API_KEY not set. Get a key at https://openrouter.ai/keys "
                "and add OPENROUTER_API_KEY=sk-or-... to your .env file."
            )
        # If a custom model is set, put it first in the chain then append the rest
        primary = model or os.environ.get("OPENROUTER_MODEL") or self.DEFAULT_MODEL
        rest = [m for m in self.FALLBACK_MODELS if m != primary]
        self._models = [primary] + rest

        self._timeout = timeout_s
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            timeout=self._timeout,
        )
        logger.info(
            f"OpenRouter provider ready (primary: {self._models[0]}, fallbacks: {self._models[1:]})"
        )

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()
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

        for model in self._models:
            payload = {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.9,
            }
            if json_mode:
                payload["response_format"] = {"type": "json_object"}

            try:
                resp = await self._client.post(self.API_URL, json=payload)
                if resp.status_code == 429:
                    logger.warning(f"OpenRouter rate limit hit on {model} — trying next fallback")
                    continue
                resp.raise_for_status()
                data = resp.json()
                # OpenRouter can return 200 with an error body (e.g. model offline)
                if "error" in data:
                    logger.warning(f"OpenRouter error on {model}: {data['error']} — trying next fallback")
                    continue
                text = data["choices"][0]["message"]["content"]
                if model != self._models[0]:
                    logger.debug(f"OpenRouter fallback model used: {model}")
                return text.strip() if text else None
            except httpx.TimeoutException:
                logger.warning(f"OpenRouter timeout on {model} — trying next fallback")
                continue
            except Exception as e:
                logger.error(f"OpenRouter error on {model}: {e}")
                return None

        logger.error("OpenRouter: all models exhausted (rate limited). Skipping comment.")
        return None
