"""
Mistral Provider

AI comment generation via Mistral's La Plateforme chat completions API.
Free tier available (the "Experiment" plan) — note its rate limits are
account-wide (~1 req/s), not per-model, so switching models inside Mistral
rarely clears a 429. Use LLM_PROVIDER=dynamic to switch PROVIDERS instead.

Get an API key at: https://console.mistral.ai/api-keys
Set in your .env file:
  LLM_PROVIDER=mistral
  MISTRAL_API_KEY=...
  MISTRAL_MODEL=mistral-small-latest   # optional

Model IDs change over time — check https://docs.mistral.ai/getting-started/models/
"""

from __future__ import annotations

import os
from typing import Optional

import httpx
from loguru import logger


class MistralProvider:
    """
    Generate text via Mistral's chat completions API with automatic
    model fallback on rate limits. Same interface as GroqProvider.
    """

    API_URL = "https://api.mistral.ai/v1/chat/completions"

    DEFAULT_MODEL = "mistral-small-latest"

    FALLBACK_MODELS = [
        "mistral-small-latest",
        "open-mistral-nemo",
        "ministral-8b-latest",
    ]

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout_s: int = 30,
    ):
        self._api_key = api_key or os.environ.get("MISTRAL_API_KEY")
        if not self._api_key:
            raise ValueError(
                "MISTRAL_API_KEY not set. Get a key at https://console.mistral.ai/api-keys "
                "and add MISTRAL_API_KEY=... to your .env file."
            )
        # If a custom model is set, put it first in the chain then append the rest
        primary = model or os.environ.get("MISTRAL_MODEL") or self.DEFAULT_MODEL
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
            f"Mistral provider ready (primary: {self._models[0]}, fallbacks: {self._models[1:]})"
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
                    logger.warning(f"Mistral rate limit hit on {model} — trying next fallback")
                    continue
                resp.raise_for_status()
                data = resp.json()
                text = data["choices"][0]["message"]["content"]
                if not text or not text.strip():
                    logger.warning(f"Mistral empty response from {model} — trying next fallback")
                    continue
                fallback = " (fallback)" if model != self._models[0] else ""
                logger.info(f"✅ LLM reply via mistral / {model}{fallback}")
                return text.strip()
            except httpx.TimeoutException:
                logger.warning(f"Mistral timeout on {model} — trying next fallback")
                continue
            except Exception as e:
                # e.g. 404 when a model alias is retired — never let one dead
                # model kill the whole chain.
                logger.warning(f"Mistral error on {model}: {e} — trying next fallback")
                continue

        logger.error("Mistral: all models exhausted (rate limited or errored). Skipping comment.")
        return None
