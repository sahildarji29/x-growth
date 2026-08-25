"""
Dynamic Provider (multi-provider failover)

Routes generation across Groq, OpenRouter, and Mistral, switching PROVIDER
when the active one fails (rate limit, timeout, API error). Each underlying
provider still runs its own model fallback chain first, so the escalation is:

  model 1 → model 2 → model 3 (same provider) → NEXT PROVIDER → ...

Set in your .env file:
  LLM_PROVIDER=dynamic
  # keys for every provider you want in the rotation (missing ones are skipped):
  GROQ_API_KEY=gsk_...
  OPENROUTER_API_KEY=sk-or-...
  MISTRAL_API_KEY=...

Optional tuning:
  DYNAMIC_PROVIDER_ORDER=groq,openrouter,mistral   # priority order
  DYNAMIC_PROVIDER_COOLDOWN_S=900                  # rest period after a failure

A provider that fails is put on cooldown so the bot doesn't hammer a
rate-limited API every call; the last provider that worked stays active
(sticky) until it fails. If every provider is cooling down, they are all
retried anyway — a stale 429 is better than silently generating nothing.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path
from typing import Optional

from loguru import logger

_HERE = Path(__file__).parent

# provider name -> (module file, class name, API-key env var)
_REGISTRY = {
    "groq": ("groq_provider.py", "GroqProvider", "GROQ_API_KEY"),
    "openrouter": ("openrouter_provider.py", "OpenRouterProvider", "OPENROUTER_API_KEY"),
    "mistral": ("mistral_provider.py", "MistralProvider", "MISTRAL_API_KEY"),
}


def _load_sibling(filename: str):
    """Load a sibling provider module by file path (avoids the heavy
    xeepy.ai.providers package __init__, matching growth_bot.py's loader)."""
    name = f"xeepy.ai.providers.{filename.removesuffix('.py')}"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = name.rsplit(".", 1)[0]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class DynamicProvider:
    """
    Failover router over the individual providers. Same interface as
    GroqProvider so CommentGenerator can use it interchangeably.
    """

    DEFAULT_ORDER = ["groq", "openrouter", "mistral"]
    DEFAULT_COOLDOWN_S = 900.0

    def __init__(self, timeout_s: int = 30):
        order_env = os.environ.get("DYNAMIC_PROVIDER_ORDER", "")
        order = [p.strip().lower() for p in order_env.split(",") if p.strip()] or self.DEFAULT_ORDER

        try:
            self._cooldown_s = float(os.environ.get("DYNAMIC_PROVIDER_COOLDOWN_S", self.DEFAULT_COOLDOWN_S))
        except ValueError:
            self._cooldown_s = self.DEFAULT_COOLDOWN_S

        self._providers: list[tuple[str, object]] = []
        for name in order:
            entry = _REGISTRY.get(name)
            if not entry:
                logger.warning(f"Dynamic: unknown provider '{name}' in DYNAMIC_PROVIDER_ORDER — skipping")
                continue
            filename, cls_name, key_env = entry
            if not os.environ.get(key_env):
                logger.warning(f"Dynamic: {key_env} not set — '{name}' excluded from rotation")
                continue
            cls = getattr(_load_sibling(filename), cls_name)
            # Each provider reads its own model env var (GROQ_MODEL /
            # OPENROUTER_MODEL / MISTRAL_MODEL) and its own fallback chain.
            self._providers.append((name, cls(timeout_s=timeout_s)))

        if not self._providers:
            names = ", ".join(_REGISTRY)
            raise ValueError(
                "LLM_PROVIDER=dynamic needs at least one API key set "
                f"(GROQ_API_KEY, OPENROUTER_API_KEY, or MISTRAL_API_KEY). Providers: {names}."
            )

        self._active = 0  # index of the provider that last worked (sticky)
        self._cooldown_until: dict[str, float] = {}

    async def start(self) -> None:
        for _, provider in self._providers:
            await provider.start()
        logger.info(
            "Dynamic provider ready — rotation: "
            + " → ".join(name for name, _ in self._providers)
            + f" (cooldown after failure: {int(self._cooldown_s)}s)"
        )

    async def stop(self) -> None:
        for _, provider in self._providers:
            await provider.stop()

    async def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        *,
        json_mode: bool = False,
        max_tokens: int = 80,
    ) -> Optional[str]:
        """
        Try the active provider first, then rotate through the rest on any
        failure. Returns None only when every provider fails.
        """
        now = time.monotonic()
        n = len(self._providers)
        # Rotation starting at the sticky active provider
        indices = [(self._active + i) % n for i in range(n)]
        candidates = [i for i in indices if self._cooldown_until.get(self._providers[i][0], 0.0) <= now]
        if not candidates:
            logger.warning("Dynamic: all providers on cooldown — retrying them anyway")
            candidates = indices

        for i in candidates:
            name, provider = self._providers[i]
            result = await provider.generate(
                prompt, system_prompt, json_mode=json_mode, max_tokens=max_tokens
            )
            if result is not None:
                self._cooldown_until.pop(name, None)
                if i != self._active:
                    logger.info(f"Dynamic: switched active provider to '{name}'")
                    self._active = i
                return result
            self._cooldown_until[name] = time.monotonic() + self._cooldown_s
            logger.warning(
                f"Dynamic: provider '{name}' failed — cooling down {int(self._cooldown_s)}s, switching"
            )

        logger.error("Dynamic: all providers failed. Skipping comment.")
        return None
