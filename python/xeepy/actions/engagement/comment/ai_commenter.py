"""
AI Commenter Action

Generates contextual comments via Claude claude-sonnet-4-6.

Safety rules enforced:
- Never posts fallback templates — skips entirely on API failure
- Generates 3 variants per tweet, picks one at random
- Never starts with "Great post!", "Great insight!", "Amazing!", etc.
- No hashtags in comments
- Skips tweets with sensitive content warnings
- Checks daily cap via SafetyMonitor before every post
"""

from __future__ import annotations

import asyncio
import os
import random
import re
import time
from typing import Callable, Optional

from loguru import logger

from xeepy.actions.base import BaseAction, BrowserManager, RateLimiter
from xeepy.actions.types import CommentResult, AutoCommentConfig, TweetElement
from xeepy.safety_monitor import SafetyMonitor

try:
    import anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False


# ---------------------------------------------------------------------------
# Comment-quality system prompt — enforced on every request
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are a developer and AI practitioner engaging genuinely on X (Twitter).
Your audience focuses on: AI, AI Automation, OpenSource, Laravel, LLM, coding agents, \
AI models, developer tools, agentic AI, vibe coding, and the future of software development.

Rules you MUST follow for every comment:
1. 1–2 sentences only. Never exceed 240 characters.
2. Be specific to the tweet's content — reference something concrete from it.
3. Add genuine value: ask a sharp question, share a brief insight, or make a relevant point.
4. Never use hashtags.
5. Never start with: "Great post", "Great insight", "Amazing", "Awesome", \
"Love this", "This is great", "So true", "Well said", "Excellent", \
"Fantastic", "Wonderful", "Incredible", "Brilliant".
6. Use emojis only when they arise naturally (max 1 per comment).
7. Write in a direct, conversational developer voice — not corporate.
8. If the tweet is low-quality, spammy, or has no concrete content to respond to, \
reply with exactly: SKIP

Output: plain text only. No quotes. No preamble. No "Reply:" label."""

# Banned opener patterns — double-safety-net on top of the system prompt
_BANNED_OPENERS = re.compile(
    r"^(great\s+(post|insight|thread|point|take)|amazing|awesome|love\s+this|"
    r"this\s+is\s+great|so\s+true|well\s+said|excellent|fantastic|wonderful|"
    r"incredible|brilliant|wow|nice\s+(one|post|thread))",
    re.IGNORECASE,
)

# Selector for Twitter's sensitive-content warning overlay
_SENSITIVE_SELECTOR = '[data-testid="sensitive-warning"]'


class AICommenter(BaseAction):
    """
    Post AI-generated comments using Claude claude-sonnet-4-6.

    Usage:
        monitor = SafetyMonitor()
        commenter = AICommenter(browser, rate_limiter, safety_monitor=monitor)
        result = await commenter.execute(
            config=AutoCommentConfig(
                keywords=["AI", "LLM", "agentic"],
                max_comments_per_session=8,
            ),
            duration_minutes=120,
        )
    """

    SELECTORS = {
        "tweet_article":  'article[data-testid="tweet"]',
        "reply_button":   '[data-testid="reply"]',
        "reply_input":    '[data-testid="tweetTextarea_0"]',
        "reply_submit":   '[data-testid="tweetButtonInline"]',
        "tweet_text":     '[data-testid="tweetText"]',
        "user_name":      '[data-testid="User-Name"]',
        "sensitive":      '[data-testid="sensitive-warning"]',
    }

    SEARCH_URL = "https://x.com/search"

    def __init__(
        self,
        browser: BrowserManager,
        rate_limiter: Optional[RateLimiter] = None,
        safety_monitor: Optional[SafetyMonitor] = None,
        dry_run: bool = False,
    ):
        self.browser = browser
        self.rate_limiter = rate_limiter
        self.safety_monitor = safety_monitor or SafetyMonitor()
        self.dry_run = dry_run
        self._cancelled = False
        self._commented_tweets: set[str] = set()
        self._session_comments = 0
        self._client: Optional[anthropic.AsyncAnthropic] = None

    def _get_client(self) -> anthropic.AsyncAnthropic:
        if not _ANTHROPIC_AVAILABLE:
            raise RuntimeError(
                "anthropic package not installed. Run: pip install anthropic"
            )
        if self._client is None:
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                raise RuntimeError(
                    "ANTHROPIC_API_KEY environment variable is not set."
                )
            self._client = anthropic.AsyncAnthropic(api_key=api_key)
        return self._client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute(
        self,
        config: AutoCommentConfig,
        duration_minutes: int = 60,
        on_comment: Optional[Callable] = None,
        on_skip: Optional[Callable] = None,
    ) -> CommentResult:
        """
        Run AI-powered auto-commenter for `duration_minutes`.

        Respects the daily cap enforced by SafetyMonitor.
        Skips entirely if Claude API is unavailable — never posts fallback text.
        """
        start_time = time.time()
        end_time = (
            start_time + duration_minutes * 60
            if duration_minutes > 0
            else float("inf")
        )
        result = CommentResult()
        self._cancelled = False
        self._commented_tweets.clear()
        self._session_comments = 0

        logger.info(
            f"AI commenter starting — duration={duration_minutes}m "
            f"max_per_session={config.max_comments_per_session}"
        )

        try:
            while not self._cancelled and time.time() < end_time:
                if self._session_comments >= config.max_comments_per_session:
                    logger.info("Session comment cap reached.")
                    break

                # Check global cooldown before navigating
                if self.safety_monitor.is_in_cooldown():
                    logger.warning("Global cooldown active — pausing commenter.")
                    await asyncio.sleep(60)
                    continue

                await self._navigate_to_source(config)
                await asyncio.sleep(random.uniform(2, 4))

                comments_this_round = await self._process_feed(
                    config=config,
                    result=result,
                    on_comment=on_comment,
                    on_skip=on_skip,
                )

                if comments_this_round == 0:
                    await self._scroll_down()

                # Human-like inter-round delay
                delay = random.uniform(*config.delay_range)
                await asyncio.sleep(delay)

        except Exception as e:
            logger.error(f"AI commenter error: {e}")
            result.errors.append(str(e))

        result.duration_seconds = time.time() - start_time
        result.cancelled = self._cancelled
        logger.info(
            f"AI commenter done — {result.success_count} posted, "
            f"{result.skipped_count} skipped, {result.failed_count} failed"
        )
        return result

    # ------------------------------------------------------------------
    # Comment generation
    # ------------------------------------------------------------------

    async def generate_comment(
        self,
        tweet: TweetElement,
        max_length: int = 240,
    ) -> Optional[str]:
        """
        Generate a comment via Claude claude-sonnet-4-6.

        Returns None (do not post) if:
        - Tweet text is empty
        - Claude API is unavailable
        - All 3 generated variants are low quality or SKIP-flagged
        - No variant passes the banned-opener check

        Never returns a hardcoded fallback string.
        """
        if not tweet.text or not tweet.text.strip():
            return None

        user_prompt = (
            f'Tweet by @{tweet.author_username or "unknown"}:\n'
            f'"{tweet.text.strip()}"\n\n'
            f"Generate 3 different reply options. Output each on its own line, "
            f"numbered 1. 2. 3. — nothing else."
        )

        try:
            client = self._get_client()
        except RuntimeError as e:
            logger.error(f"Claude client unavailable: {e} — skipping comment.")
            return None

        try:
            response = await client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=300,
                temperature=1.0,  # max diversity across variants
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw = response.content[0].text.strip()
        except anthropic.RateLimitError as e:
            logger.error(f"Anthropic rate limit: {e}")
            await self.safety_monitor.trigger_cooldown(f"Anthropic RateLimitError: {e}", seconds=3600)
            return None
        except Exception as e:
            logger.error(f"Claude API error: {e} — skipping comment (no fallback).")
            return None

        # Parse numbered variants
        variants: list[str] = []
        for line in raw.splitlines():
            stripped = re.sub(r"^\d+[\.\)]\s*", "", line.strip())
            if stripped and stripped.upper() != "SKIP" and len(stripped) > 10:
                variants.append(stripped)

        if not variants:
            logger.info("All Claude variants were SKIP or empty — skipping.")
            return None

        # Shuffle and pick first that passes quality gate
        random.shuffle(variants)
        for candidate in variants:
            if _BANNED_OPENERS.match(candidate):
                logger.debug(f"Variant rejected (banned opener): {candidate[:60]}")
                continue
            if "#" in candidate:
                candidate = re.sub(r"\s*#\w+", "", candidate).strip()
            if len(candidate) > max_length:
                candidate = candidate[:max_length - 1] + "…"
            return candidate

        logger.info("No variant passed quality gate — skipping.")
        return None

    # ------------------------------------------------------------------
    # Feed processing
    # ------------------------------------------------------------------

    async def _process_feed(
        self,
        config: AutoCommentConfig,
        result: CommentResult,
        on_comment: Optional[Callable],
        on_skip: Optional[Callable],
    ) -> int:
        comments_this_round = 0
        tweets = await self._get_visible_tweets()

        for tweet_element in tweets:
            if self._cancelled:
                break
            if self._session_comments >= config.max_comments_per_session:
                break

            # Skip sensitive content
            if await self._is_sensitive(tweet_element):
                result.skipped_count += 1
                logger.debug("Skipping tweet: sensitive content flag")
                continue

            tweet = await self._parse_tweet_element(tweet_element)
            if not tweet or not tweet.text:
                continue

            tweet_id = tweet.tweet_url or str(id(tweet_element))
            if tweet_id in self._commented_tweets:
                continue
            self._commented_tweets.add(tweet_id)

            if not self._matches_targeting(tweet, config):
                result.skipped_count += 1
                if on_skip:
                    await self._safe_callback(on_skip, tweet, "no match")
                continue

            # Check daily cap via SafetyMonitor BEFORE generating the comment
            allowed = await self.safety_monitor.record(
                "comment",
                target=tweet_id,
                note=f"tweet by @{tweet.author_username}",
            )
            if not allowed:
                logger.info("Daily comment cap reached — stopping commenter.")
                self._cancelled = True
                break

            # Generate via Claude — never falls back to templates
            comment_text = await self.generate_comment(tweet)
            if not comment_text:
                # Undo the counter increment (we didn't post)
                await self.safety_monitor.record_outcome("comment", tweet_id, False, "skipped_no_text")
                result.skipped_count += 1
                continue

            # Rate limiting between comments
            if self.rate_limiter:
                await self.rate_limiter.wait()

            success = await self._post_comment(tweet_element, comment_text)
            await self.safety_monitor.record_outcome("comment", tweet_id, success)

            if success:
                result.success_count += 1
                result.comments_posted.append(
                    {"tweet_url": tweet_id, "comment_text": comment_text, "ai_generated": True}
                )
                self._session_comments += 1
                comments_this_round += 1
                logger.info(f"Posted comment: {comment_text[:80]}…")
                if on_comment:
                    await self._safe_callback(on_comment, tweet, comment_text)
                # Post-comment pause: longer than between-like pauses
                await asyncio.sleep(random.uniform(30, 75))
            else:
                result.failed_count += 1

        return comments_this_round

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _is_sensitive(self, tweet_element) -> bool:
        """Return True if the tweet has a sensitive-content warning."""
        try:
            warning = await tweet_element.query_selector(self.SELECTORS["sensitive"])
            return warning is not None
        except Exception:
            return False

    def _matches_targeting(self, tweet: TweetElement, config: AutoCommentConfig) -> bool:
        if config.from_users and tweet.author_username:
            if tweet.author_username.lower() in {u.lower() for u in config.from_users}:
                return True
        if config.keywords and tweet.text:
            if any(kw.lower() in tweet.text.lower() for kw in config.keywords):
                return True
        if config.hashtags and tweet.hashtags:
            tweet_tags = {h.lower() for h in tweet.hashtags}
            if any(h.lower().lstrip("#") in tweet_tags for h in config.hashtags):
                return True
        if not config.keywords and not config.hashtags and not config.from_users:
            return True
        return False

    async def _post_comment(self, tweet_element, comment_text: str) -> bool:
        if self.dry_run:
            logger.info(f"[DRY-RUN] Would post: {comment_text}")
            return True
        try:
            page = self._get_page()
            if not page:
                return False
            reply_btn = await tweet_element.query_selector(self.SELECTORS["reply_button"])
            if not reply_btn:
                return False
            await reply_btn.click()
            await asyncio.sleep(random.uniform(1.0, 2.0))
            await page.wait_for_selector(self.SELECTORS["reply_input"], timeout=5000)
            reply_input = await page.query_selector(self.SELECTORS["reply_input"])
            if reply_input:
                await reply_input.click()
                # Type at human speed: 40–80 ms per character
                await page.keyboard.type(comment_text, delay=random.randint(40, 80))
                await asyncio.sleep(random.uniform(0.5, 1.5))
            submit_btn = await page.query_selector(self.SELECTORS["reply_submit"])
            if submit_btn:
                await submit_btn.click()
                await asyncio.sleep(2)
                return True
        except Exception as e:
            logger.error(f"Error posting comment: {e}")
        return False

    async def _navigate_to_source(self, config: AutoCommentConfig) -> None:
        if config.keywords:
            kw = random.choice(config.keywords)
            await self.browser.goto(
                f"{self.SEARCH_URL}?q={kw}&src=typed_query&f=live"
            )
        elif config.hashtags:
            tag = random.choice(config.hashtags).lstrip("#")
            await self.browser.goto(
                f"{self.SEARCH_URL}?q=%23{tag}&src=typed_query&f=live"
            )
        else:
            await self.browser.goto("https://x.com/home")

    async def _get_visible_tweets(self) -> list:
        page = self._get_page()
        if not page:
            return []
        try:
            return await page.query_selector_all(self.SELECTORS["tweet_article"])
        except Exception:
            return []

    async def _parse_tweet_element(self, element) -> Optional[TweetElement]:
        try:
            tweet = TweetElement()
            text_el = await element.query_selector('[data-testid="tweetText"]')
            if text_el:
                tweet.text = await text_el.text_content()
                tweet.has_text = bool(tweet.text and tweet.text.strip())
            user_el = await element.query_selector(self.SELECTORS["user_name"])
            if user_el:
                user_text = await user_el.text_content()
                if "@" in user_text:
                    parts = user_text.split("@")
                    if len(parts) >= 2:
                        tweet.author_display_name = parts[0].strip()
                        tweet.author_username = parts[1].split()[0].strip("·").strip()
            link_el = await element.query_selector('a[href*="/status/"]')
            if link_el:
                href = await link_el.get_attribute("href")
                if href:
                    tweet.tweet_url = (
                        f"https://x.com{href}" if href.startswith("/") else href
                    )
            if tweet.text:
                tweet.hashtags = re.findall(r"#(\w+)", tweet.text)
            return tweet
        except Exception:
            return None

    async def _scroll_down(self, pixels: int = 800) -> None:
        page = self._get_page()
        if page:
            await page.evaluate(f"window.scrollBy(0, {pixels})")
            await asyncio.sleep(0.5)

    def _get_page(self):
        return getattr(self.browser, "page", None)

    async def _safe_callback(self, callback: Callable, *args) -> None:
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(*args)
            else:
                callback(*args)
        except Exception as e:
            logger.error(f"Callback error: {e}")

    def cancel(self) -> None:
        self._cancelled = True
