"""
Claude Session Provider

Generates text by driving the claude.ai web UI with Playwright.
No API key required — uses a saved browser profile (persistent login).

First-time setup (run once, headed browser):
    from xeepy.ai.providers.claude_session import ClaudeSessionProvider
    import asyncio
    provider = ClaudeSessionProvider()
    asyncio.run(provider.setup())   # opens chrome, you log in, profile is saved

Subsequent runs (headless):
    provider = ClaudeSessionProvider(headless=True)
    await provider.start()
    comment = await provider.generate("Tweet text here...")
    await provider.stop()
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Optional

from loguru import logger

try:
    from playwright.async_api import async_playwright, Page, BrowserContext
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False


_PROFILE_DIR = Path(os.environ.get("CLAUDE_PROFILE_DIR", "./data/claude_profile"))

# How many characters of the prompt to search for in the page text
_PROMPT_ANCHOR_LEN = 80


class ClaudeSessionProvider:
    """
    AI provider that drives claude.ai via Playwright.

    - Uses a persistent browser profile so the session survives restarts.
    - Navigates to /new, types the prompt, waits for response to stabilise.
    - Strips UI chrome ("Claude", "Response from Claude:") before returning.
    """

    def __init__(
        self,
        profile_dir: Path | str | None = None,
        headless: bool = True,
        timeout_s: int = 90,
    ):
        if not _PLAYWRIGHT_AVAILABLE:
            raise ImportError(
                "playwright not installed. Run: pip install playwright && playwright install chromium"
            )
        self.profile_dir = Path(profile_dir or _PROFILE_DIR)
        self.headless = headless
        self.timeout_s = timeout_s
        self._playwright = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def setup(self) -> None:
        """
        First-time setup — launches a headed browser so you can log in to
        claude.ai manually. Saves the session to profile_dir on close.
        """
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Opening claude.ai — log in, then close the browser window.")
        logger.info(f"Session will be saved to: {self.profile_dir}")

        async with async_playwright() as pw:
            ctx = await pw.chromium.launch_persistent_context(
                str(self.profile_dir),
                headless=False,
                channel="chrome",
            )
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto("https://claude.ai")

            logger.info("=" * 60)
            logger.info("  Chrome is open at claude.ai on your desktop.")
            logger.info("  1. Log in with Google or email.")
            logger.info("  2. Wait until the Claude chat page fully loads.")
            logger.info("  3. Come back here — this script saves and exits automatically.")
            logger.info("  Waiting up to 10 minutes for login...")
            logger.info("=" * 60)

            # Poll until the URL indicates a successful login (chat page loaded)
            deadline = asyncio.get_event_loop().time() + 600
            logged_in = False
            while asyncio.get_event_loop().time() < deadline:
                try:
                    url = page.url
                    # claude.ai shows /new or /chat/... after login
                    if "claude.ai" in url and ("/new" in url or "/chat" in url or "chats" in url):
                        logger.info(f"Login detected at: {url}")
                        logged_in = True
                        break
                except Exception:
                    pass
                await asyncio.sleep(1)

            if logged_in:
                logger.info("Saving session — do not close the browser yet...")
                await asyncio.sleep(3)  # let cookies flush to disk
            else:
                logger.warning("Timed out waiting for login. Saving whatever state exists.")

            await ctx.close()  # explicit close ensures cookies are written to profile

        logger.info("Claude session saved. Re-run without setup flag to start the bot.")

    async def start(self) -> None:
        """Launch the persistent context and navigate to claude.ai."""
        if not self.profile_dir.exists():
            raise RuntimeError(
                f"Claude profile not found at {self.profile_dir}. "
                "Run provider.setup() first to log in."
            )

        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            str(self.profile_dir),
            headless=self.headless,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        await self._page.goto("https://claude.ai/new")
        await asyncio.sleep(2)
        logger.info("Claude session started.")

    async def stop(self) -> None:
        if self._context:
            await self._context.close()
            self._context = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        self._page = None
        logger.info("Claude session stopped.")

    # ------------------------------------------------------------------
    # Text generation
    # ------------------------------------------------------------------

    async def generate(self, prompt: str, system_prompt: str = "") -> Optional[str]:
        """
        Send `prompt` to claude.ai and return the response text.

        Returns None if the model refuses, times out, or any error occurs.
        Never returns a fallback string.
        """
        if not self._page:
            raise RuntimeError("Provider not started. Call start() first.")

        page = self._page
        full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
        truncated = full_prompt[:2000]

        try:
            # 1. Navigate to /new for a fresh conversation
            await page.goto("https://claude.ai/new", wait_until="domcontentloaded")
            await asyncio.sleep(1.5)

            # 2. Find the input
            input_el = None
            for sel in [
                'div.ProseMirror[contenteditable="true"]',
                'div[contenteditable="true"]',
                'textarea',
            ]:
                try:
                    input_el = await page.wait_for_selector(sel, timeout=8000)
                    if input_el:
                        break
                except Exception:
                    continue

            if not input_el:
                logger.error("Claude: could not find input element")
                return None

            # 3. Type the prompt
            await input_el.click()
            await input_el.fill("")
            await page.keyboard.type(truncated, delay=15)
            await asyncio.sleep(0.4)

            # 4. Send — click button or press Enter
            sent = False
            for btn_sel in [
                'button[aria-label="Send message"]',
                '[data-testid="send-button"]',
                'button[type="submit"]',
            ]:
                try:
                    btn = await page.query_selector(btn_sel)
                    if btn and not await btn.is_disabled():
                        await btn.click()
                        sent = True
                        break
                except Exception:
                    continue

            if not sent:
                await page.keyboard.press("Enter")

            await asyncio.sleep(3)

            # 5. Wait for response to appear and stabilise
            response_text = await self._wait_for_response(page, truncated, self.timeout_s)
            if not response_text:
                logger.warning("Claude: no response extracted")
                return None

            # 6. Clean up UI chrome
            cleaned = self._clean_response(response_text)
            logger.debug(f"Claude response ({len(cleaned)} chars): {cleaned[:80]}")
            return cleaned or None

        except Exception as e:
            logger.error(f"Claude generate error: {e}")
            return None

    async def _wait_for_response(
        self,
        page: Page,
        prompt_sent: str,
        timeout_s: int,
    ) -> str:
        """
        Poll until the response text appears and stops changing (stable for 2s).
        Falls back to extracting text after the prompt anchor in the page body.
        """
        prev = ""
        stable_count = 0
        deadline = asyncio.get_event_loop().time() + timeout_s
        anchor = prompt_sent[:_PROMPT_ANCHOR_LEN]

        while asyncio.get_event_loop().time() < deadline:
            text = await self._extract_response(page, anchor)
            if text:
                if text == prev:
                    stable_count += 1
                    if stable_count >= 4:  # unchanged for 2 seconds
                        return text
                else:
                    stable_count = 0
                    prev = text
            await asyncio.sleep(0.5)

        return prev  # return whatever we had at timeout

    async def _extract_response(self, page: Page, anchor: str) -> str:
        """Try several strategies to extract Claude's response text."""

        # Strategy 1: specific data attribute selectors
        for sel in [
            '[data-message-author-role="assistant"] .whitespace-pre-wrap',
            '[data-message-author-role="assistant"] .prose',
            '[data-message-author-role="assistant"]',
        ]:
            try:
                els = await page.query_selector_all(sel)
                if els:
                    text = (await els[-1].inner_text()).strip()
                    if len(text) > 15:
                        return text
            except Exception:
                pass

        # Strategy 2: find the response after the prompt anchor in page text
        try:
            body = await page.evaluate(
                "(anchor) => {"
                "  const root = document.querySelector('main') || document.body;"
                "  const full = root.innerText || '';"
                "  const idx = full.lastIndexOf(anchor);"
                "  return idx >= 0 ? full.slice(idx + anchor.length).trim() : '';"
                "}",
                anchor,
            )
            if body and len(body) > 15:
                return body
        except Exception:
            pass

        return ""

    def _clean_response(self, text: str) -> str:
        """Strip UI labels and whitespace from the extracted response."""
        # Remove "Response from Claude:" / "Claude:" prefixes
        text = re.sub(r"^(response from claude|claude|assistant)\s*:?\s*", "", text, flags=re.IGNORECASE)
        # Remove trailing UI chrome (copy/thumbs buttons appear as text)
        text = re.sub(r"\s*(Copy|Thumb up|Thumb down|Regenerate|Stop)\s*$", "", text, flags=re.IGNORECASE)
        # Collapse excessive newlines
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
