"""
XActions Growth Bot
====================
Full automation workflow: likes + AI comments + follows using your Twitter
and Claude sessions. No API keys required.

Usage:
  # Step 1 — first time only: save Claude session
  python growth_bot.py --setup-claude

  # Step 2 — run the bot
  python growth_bot.py

  # Dry run (no actual posts)
  python growth_bot.py --dry-run

  # Customise targets
  python growth_bot.py --comments 200 --likes 300 --follows 100 --hours 13

Config via .env or environment variables:
  XEEPY_SESSION_FILE   Path to twitter session.json  (default: ./data/session.json)
  CLAUDE_PROFILE_DIR   Path to claude browser profile (default: ./data/claude_profile)
  TARGET_KEYWORDS      Comma-separated search keywords
  TARGET_ACCOUNTS      Comma-separated @handles to target followers of
  BOT_HEADLESS         true/false (default: true)
  BOT_ACTIVE_START     Hour to start (default: 9)
  BOT_ACTIVE_END       Hour to stop  (default: 22)
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import importlib.util
import json
import os
import random
import re
import sys
import time
import types
from dataclasses import dataclass, field
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from langdetect import detect as _lang_detect, LangDetectException
from loguru import logger

load_dotenv()

# Register a lightweight xeepy namespace so sub-module imports work without
# triggering xeepy/__init__.py's heavy import chain (which has broken deps).
_ROOT = Path(__file__).parent
if "xeepy" not in sys.modules:
    _pkg = types.ModuleType("xeepy")
    _pkg.__path__ = [str(_ROOT / "xeepy")]
    _pkg.__package__ = "xeepy"
    sys.modules["xeepy"] = _pkg


def _load_module(name: str, rel_path: str):
    """Load a sub-module by file path, registering it under `name`."""
    if name in sys.modules:
        return sys.modules[name]
    path = _ROOT / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = name.rsplit(".", 1)[0]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

# ── paths ──────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent
DATA_DIR = Path(os.environ.get("XEEPY_DATA_DIR", ROOT / "data"))
LOG_DIR = Path(os.environ.get("XEEPY_LOG_DIR", ROOT / "logs"))
SESSION_FILE = Path(os.environ.get("XEEPY_SESSION_FILE", DATA_DIR / "session.json"))
CLAUDE_PROFILE = Path(os.environ.get("CLAUDE_PROFILE_DIR", DATA_DIR / "claude_profile"))
CONFIG_DIR = Path(os.environ.get("XEEPY_CONFIG_DIR", ROOT / "config"))


# ── env helpers ──────────────────────────────────────────────────────────────
def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (ValueError, TypeError):
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except (ValueError, TypeError):
        return default


def _env_list(key: str, default: list[str]) -> list[str]:
    """Comma-separated env value → list of trimmed strings (falls back to default)."""
    raw = os.environ.get(key)
    if raw is None:
        return list(default)
    items = [x.strip() for x in raw.split(",") if x.strip()]
    return items or list(default)


def _resolve_path(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else (ROOT / p)


def _load_text(path_env: str, inline_env: str, default: str) -> str:
    """Load a text blob (persona/prompt). Priority: inline env var → file path → built-in default."""
    inline = (os.environ.get(inline_env) or "").strip()
    if inline:
        return inline
    path_val = (os.environ.get(path_env) or "").strip()
    if path_val:
        p = _resolve_path(path_val)
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
        logger.warning(f"{path_env}={path_val} not found — using built-in default persona")
    return default


# ── targeting / identity (user-configurable) ────────────────────────────────
_DEFAULT_KEYWORDS = [
    "AI automation", "LLM agents", "agentic AI", "vibe coding", "coding agents",
    "LaraCopilot", "Laravel AI", "developer tools", "open source AI",
    "Claude AI", "GPT automation", "agent orchestration",
]

KEYWORDS: list[str] = _env_list("TARGET_KEYWORDS", _DEFAULT_KEYWORDS)

TARGET_ACCOUNTS: list[str] = [
    a.lstrip("@") for a in _env_list("TARGET_ACCOUNTS", [])
]

ACTIVE_START = _env_int("BOT_ACTIVE_START", 9)
ACTIVE_END = _env_int("BOT_ACTIVE_END", 22)
HEADLESS = os.environ.get("BOT_HEADLESS", "true").lower() != "false"
MY_HANDLE = os.environ.get("XEEPY_USERNAME", "").strip().lstrip("@").lower()

# ── original-post (randomized daily count) settings ─────────────────────────
POST_TARGET_FILE = Path(os.environ.get("XEEPY_POST_TARGET_FILE", DATA_DIR / "post_target.json"))
SAFETY_MAX_POSTS_DAY = _env_int("SAFETY_MAX_POSTS_DAY", 3)
POSTS_PER_DAY_MIN = _env_int("POSTS_PER_DAY_MIN", 2)
POSTS_PER_DAY_MAX = _env_int("POSTS_PER_DAY_MAX", 3)

# ── language filter ─────────────────────────────────────────────────────────
def is_english(text: str) -> bool:
    """Return True only if the text is detected as English."""
    if not text or len(text.strip()) < 15:
        return False
    try:
        return _lang_detect(text) == "en"
    except LangDetectException:
        return False


# ── persona for comment generation ──────────────────────────────────────────
# Built-in default persona. Each user overrides via PERSONA_COMMENT_FILE (path to
# a .md file) or the PERSONA_COMMENT inline env var. See config/persona_comment.md.
_BUILTIN_COMMENT_PERSONA = """You are Sahil Darji.

Founding Engineer at LaraCopilot. Solution Architect. Engineering Lead. Open Source Maintainer. 10+ years building production software.

Your goal is NOT to maximise replies. Your goal is to become one of the most respected engineering voices in Laravel, AI-powered software development, AI Agents, Agent Orchestration, Developer Tooling, and Engineering Leadership.

Every comment should increase trust. The ideal reaction: "That person is actually building things." Never: "That sounds like ChatGPT."

────────────────────────────────────
AUDIENCE
────────────────────────────────────

Write for: Founders, CTOs, Engineering Managers, Laravel Developers, AI Engineers, Product Engineers, Technical Architects, Startup Builders. Ignore everyone else.

────────────────────────────────────
POSITIONING
────────────────────────────────────

Your beliefs (use only when naturally relevant):
- Models are becoming commodities. Systems are the moat.
- AI augments developers. It doesn't replace engineering judgement.
- Context beats prompts. Specifications beat vague prompts.
- Developer experience matters more than benchmarks.
- Reliability beats flashy demos. Trust compounds. Good engineering outlasts model releases.

────────────────────────────────────
PRODUCTS
────────────────────────────────────

Products you build: LaraCopilot, LaraSpec, Spine, open source packages.

Do NOT promote them. Do NOT insert product names just because they exist. Mention only if: the discussion is directly about Laravel AI, someone asks, or your own experience building them adds genuine credibility. 95% of comments should NEVER mention a product.

────────────────────────────────────
COMMENT RULES
────────────────────────────────────

Maximum 160 characters. Prefer 40–120. Maximum 4 short lines. Never write essays.

Your reply MUST directly address the specific content of this tweet. Do not write generic engineering opinions that could apply to any tweet.

Every comment must add ONE of: experience, insight, trade-off, respectful disagreement, engineering lesson.

Never simply agree. Never repeat or paraphrase the tweet.

Do NOT ask a question unless it's the only way to add real value. Most comments should NOT be questions.

────────────────────────────────────
WHEN TO SKIP
────────────────────────────────────

Set relevant=false (see OUTPUT) if: spam, giveaway, politics, motivation, crypto, unrelated news, obvious engagement farming, generic self-improvement, low-effort reposts, non-English, anything outside your expertise, nothing technical or thoughtful to engage with, or you cannot add meaningful value.

Skipping is good. Quality > Quantity.

────────────────────────────────────
VOICE
────────────────────────────────────

You are NOT writing content. You're replying between meetings. You're replying while waiting for CI. You're replying after fixing a bug.

Natural. Short. Direct. Opinionated. Never polished. Never corporate. Never inspirational. Never LinkedIn. Never ChatGPT.

Good: "We hit the same wall." / "Context was the bottleneck." / "I'd optimise the workflow first." / "That's usually where things fall apart." / "Benchmarks rarely tell the whole story."

Bad: "Absolutely agree." / "Very insightful." / "This is an excellent perspective." / "I completely agree." / "It will be interesting to see."

Use contractions: I'm, we're, it's, don't, can't, wouldn't. Fragments are fine. One sentence is fine. Don't over-explain. No bullet points. No numbered lists.

────────────────────────────────────
FORBIDDEN WORDS
────────────────────────────────────

Never use: game changer, revolutionary, unlock, leverage, synergy, cutting-edge, supercharge, fascinating, indeed, certainly, absolutely, moreover, furthermore, it's worth noting, here's why, let that sink in, this changes everything, thrilled, delighted, excited to announce.

────────────────────────────────────
REPUTATION FILTER
────────────────────────────────────

Before replying ask: Would this make a senior engineer think "Interesting." — or "AI wrote this."? If it's the second, reply: SKIP

────────────────────────────────────
OUTPUT — STRICT JSON
────────────────────────────────────

Return ONLY a JSON object, nothing else:
{"relevant": true or false, "comment": "your reply here"}

- relevant=false → set comment to "". Use this whenever the WHEN TO SKIP rules apply.
- relevant=true → put the reply in comment.

When relevant=true, the comment still follows every rule above: plain text, under 160 chars, no hashtags, no author tags, no markdown, no emojis unless one genuinely fits, and it must sound like a real engineer."""

SYSTEM_PROMPT = _load_text("PERSONA_COMMENT_FILE", "PERSONA_COMMENT", _BUILTIN_COMMENT_PERSONA)


# ── persona for original (non-reply) post generation ────────────────────────
# Override via PERSONA_POST_FILE / PERSONA_POST env. See config/persona_post.md.
_BUILTIN_POST_PERSONA = """You are Sahil Darji.

Founding Engineer at LaraCopilot. Solution Architect. Engineering Lead. Open Source Maintainer. 10+ years building production software.

You are writing a brand-new, standalone tweet — not a reply to anyone. It should read like a real engineer sharing a quick, specific thought between tasks.

Same voice rules as always: natural, short, direct, opinionated. Never polished, never corporate, never inspirational, never LinkedIn, never ChatGPT. Use contractions. Fragments are fine. No bullet points, no numbered lists, no hashtags, no emojis unless one genuinely fits.

Maximum 200 characters. Prefer 60-160. One or two sentences.

Do NOT promote LaraCopilot, LaraSpec, or any product by name unless the requested angle is explicitly about building them — and even then, keep it low-key, a lesson learned rather than a pitch. Most posts should mention no product at all.

Never repeat the ideas or phrasing of your own recent posts (listed in the prompt) — say something genuinely different each time.

Never use: game changer, revolutionary, unlock, leverage, synergy, cutting-edge, supercharge, fascinating, indeed, certainly, absolutely, moreover, furthermore, it's worth noting, here's why, let that sink in, this changes everything, thrilled, delighted, excited to announce.

Output: plain text only. No quotes. No markdown. Just the tweet text."""

POST_SYSTEM_PROMPT = _load_text("PERSONA_POST_FILE", "PERSONA_POST", _BUILTIN_POST_PERSONA)


def _load_post_angles() -> list[str]:
    """Load original-post angles from POST_ANGLES_FILE (one per line) or the
    POST_ANGLES env (pipe-separated), else fall back to built-in defaults."""
    default = [
        "a specific lesson learned building your product (rare, low-key, no pitch)",
        "an opinion about your field vs. the common approach",
        "a concrete engineering tip or observation from your domain",
        "a take on the tools or developer experience in your space",
        "a short reflection on leadership or hiring in your field",
        "a trade-off you've personally hit between speed and reliability",
        "an honest opinion about the current state of your industry",
    ]
    inline = (os.environ.get("POST_ANGLES") or "").strip()
    if inline:
        items = [a.strip() for a in inline.split("|") if a.strip()]
        if items:
            return items
    path_val = (os.environ.get("POST_ANGLES_FILE") or "").strip()
    if path_val:
        p = _resolve_path(path_val)
        if p.exists():
            items = [
                ln.strip()
                for ln in p.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.strip().startswith("#")
            ]
            if items:
                return items
        else:
            logger.warning(f"POST_ANGLES_FILE={path_val} not found — using built-in angles")
    return default


POST_ANGLES = _load_post_angles()


# ── tweet relevance scorer (user-configurable keyword tiers) ─────────────────
COMMENT_SCORE_THRESHOLD = _env_int("COMMENT_SCORE_THRESHOLD", 6)

# Default tiers reproduce the original Laravel/AI-engineering targeting.
# Each user overrides via SCORE_HIGH / SCORE_MEDIUM / SCORE_NEGATIVE in .env.
_DEFAULT_SCORE_HIGH = [
    "laravel", "livewire", "filament", "eloquent", "artisan", "inertia", "pest", "php",
    "ai agent", "ai agents", "agentic", "claude code", "codex", "cursor",
    "agent orchestration", "mcp server", "llm agent", "agent pipeline",
    "open source", "developer tool", "dev tool", "devtool", "cli tool", "framework", "sdk",
]
_DEFAULT_SCORE_MEDIUM = [
    "saas", "startup", "founder", "cto", "engineering lead", "engineering manager",
    "engineering leadership", "b2b", "bootstrapped", "indie hacker",
    "llm", "gpt", "gemini", "claude", "anthropic", "openai", "rag", "fine-tune",
    "developer experience", "typescript", "node.js", "nodejs", "python", "django",
    "fastapi", "graphql", "rest api", "devops", "ci/cd", "docker", "kubernetes",
    "github actions",
]
_DEFAULT_SCORE_NEGATIVE = [
    "politics", "election", "president", "giveaway", "crypto", "bitcoin", "ethereum",
    "nft", "blockchain", "motivation", "hustle culture", "inspirational", "celebrity",
    "news alert",
]

SCORE_HIGH_POINTS = _env_int("SCORE_HIGH_POINTS", 10)
SCORE_MEDIUM_POINTS = _env_int("SCORE_MEDIUM_POINTS", 6)
SCORE_NEGATIVE_POINTS = _env_int("SCORE_NEGATIVE_POINTS", 10)


def _build_term_pattern(terms: list[str]):
    """Compile a case-insensitive matcher for a list of literal terms.
    Uses non-alphanumeric boundaries so 'ai' won't match inside 'brain' but
    still handles terms with punctuation like 'ci/cd' or 'node.js'."""
    cleaned = [re.escape(t.strip().lower()) for t in terms if t.strip()]
    if not cleaned:
        return None
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(cleaned) + r")(?![a-z0-9])", re.IGNORECASE)


_SCORE_HIGH_RE = _build_term_pattern(_env_list("SCORE_HIGH", _DEFAULT_SCORE_HIGH))
_SCORE_MEDIUM_RE = _build_term_pattern(_env_list("SCORE_MEDIUM", _DEFAULT_SCORE_MEDIUM))
_SCORE_NEGATIVE_RE = _build_term_pattern(_env_list("SCORE_NEGATIVE", _DEFAULT_SCORE_NEGATIVE))


def score_tweet(text: str) -> tuple[int, list[str]]:
    """Score a tweet for relevance from the configured keyword tiers.
    Returns (score, reasons). Only score >= COMMENT_SCORE_THRESHOLD reaches the LLM."""
    t = text.lower()
    score = 0
    reasons: list[str] = []

    if _SCORE_HIGH_RE and _SCORE_HIGH_RE.search(t):
        score += SCORE_HIGH_POINTS
        reasons.append(f"+{SCORE_HIGH_POINTS} high")

    if _SCORE_MEDIUM_RE and _SCORE_MEDIUM_RE.search(t):
        score += SCORE_MEDIUM_POINTS
        reasons.append(f"+{SCORE_MEDIUM_POINTS} medium")

    if _SCORE_NEGATIVE_RE and _SCORE_NEGATIVE_RE.search(t):
        score -= SCORE_NEGATIVE_POINTS
        reasons.append(f"-{SCORE_NEGATIVE_POINTS} negative")

    return score, reasons


# ── tweet data class ─────────────────────────────────────────────────────────
@dataclass
class Tweet:
    id: str = ""
    text: str = ""
    author: str = ""
    url: str = ""
    el: object = field(default=None, repr=False)  # Playwright ElementHandle


# ── stats ────────────────────────────────────────────────────────────────────
@dataclass
class BotStats:
    likes: int = 0
    comments: int = 0
    follows: int = 0
    posts: int = 0
    skipped: int = 0
    errors: int = 0
    started_at: float = field(default_factory=time.time)

    def report(self) -> str:
        elapsed = int(time.time() - self.started_at)
        h, m = divmod(elapsed // 60, 60)
        return (
            f"Likes={self.likes}  Comments={self.comments}  "
            f"Follows={self.follows}  Posts={self.posts}  Skipped={self.skipped}  "
            f"Errors={self.errors}  Uptime={h}h{m}m"
        )


# ── Twitter Playwright client ────────────────────────────────────────────────
class TwitterBot:
    """
    Drives Twitter via Playwright using a saved session file.
    """

    SEL = {
        "tweet":    'article[data-testid="tweet"]',
        "like_btn": '[data-testid="like"]',
        "reply":    '[data-testid="reply"]',
        "reply_input": '[data-testid="tweetTextarea_0"]',
        "reply_submit": '[data-testid="tweetButtonInline"]',
        "tweet_text": '[data-testid="tweetText"]',
        "user_name": '[data-testid="User-Name"]',
        "follow_btn": '[data-testid="follow"]',
        "sensitive": '[data-testid="sensitive-warning"]',
    }

    def __init__(self, session_file: Path, headless: bool = True, dry_run: bool = False):
        self.session_file = session_file
        self.headless = headless
        self.dry_run = dry_run
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._seen_ids: set[str] = set()

    async def start(self) -> None:
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx_opts = {}
        if self.session_file.exists():
            ctx_opts["storage_state"] = str(self.session_file)
        self._context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            # 900px avoids Twitter's two-column layout (triggers at ~1024px)
            # which opens a detail panel on click that then overlays the feed
            viewport={"width": 900, "height": 900},
            **ctx_opts,
        )
        self._page = await self._context.new_page()
        await self._page.goto("https://x.com/home", wait_until="domcontentloaded")
        await asyncio.sleep(3)
        logger.info("Twitter browser ready.")

    async def stop(self) -> None:
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    # ── feed + search ─────────────────────────────────────────────────────

    async def get_home_tweets(self, limit: int = 15) -> list[Tweet]:
        await self._page.goto("https://x.com/home", wait_until="domcontentloaded")
        try:
            await self._page.wait_for_selector(self.SEL["tweet"], timeout=12000)
        except Exception:
            logger.warning("Home feed: no tweets loaded within 12s")
            return []
        await self.dismiss_overlay()
        await asyncio.sleep(1.5)
        tweets = await self._collect_visible_tweets(limit)
        logger.info(f"Home feed: {len(tweets)} new tweets collected")
        return tweets

    async def search_tweets(self, keyword: str, limit: int = 15) -> list[Tweet]:
        q = keyword.replace(" ", "%20")
        await self._page.goto(
            f"https://x.com/search?q={q}&src=typed_query&f=live",
            wait_until="domcontentloaded",
        )
        try:
            await self._page.wait_for_selector(self.SEL["tweet"], timeout=12000)
        except Exception:
            logger.warning(f"Search '{keyword}': no tweets loaded within 12s")
            return []
        await self.dismiss_overlay()
        await asyncio.sleep(1.5)
        tweets = await self._collect_visible_tweets(limit)
        logger.info(f"Search '{keyword}': {len(tweets)} new tweets collected")
        return tweets

    async def _collect_visible_tweets(self, limit: int) -> list[Tweet]:
        tweets: list[Tweet] = []
        try:
            els = await self._page.query_selector_all(self.SEL["tweet"])
        except Exception:
            return tweets

        logger.debug(f"  raw tweet articles in DOM: {len(els)}")

        for el in els[:limit]:
            try:
                # Skip sensitive content
                if await el.query_selector(self.SEL["sensitive"]):
                    continue

                text_el = await el.query_selector(self.SEL["tweet_text"])
                if not text_el:
                    continue
                text = (await text_el.inner_text()).strip()
                if not text:
                    continue

                # Author
                author = ""
                user_el = await el.query_selector(self.SEL["user_name"])
                if user_el:
                    raw = await user_el.inner_text()
                    m = re.search(r"@(\w+)", raw)
                    if m:
                        author = m.group(1)

                # URL / ID
                url = ""
                link_el = await el.query_selector('a[href*="/status/"]')
                if link_el:
                    href = await link_el.get_attribute("href") or ""
                    url = f"https://x.com{href}" if href.startswith("/") else href

                tweet_id = url or f"{author}:{text[:40]}"
                if tweet_id in self._seen_ids:
                    continue
                self._seen_ids.add(tweet_id)

                # Attach the element handle so actions always hit the right tweet
                tweets.append(Tweet(id=tweet_id, text=text, author=author, url=url, el=el))
            except Exception:
                continue

        return tweets

    # ── actions ──────────────────────────────────────────────────────────

    async def like(self, tweet_el) -> bool:
        if self.dry_run:
            logger.info("[DRY-RUN] like")
            return True
        await self.dismiss_overlay()
        try:
            btn = await tweet_el.query_selector(self.SEL["like_btn"])
            if btn:
                await btn.click(timeout=5000)
                await asyncio.sleep(random.uniform(0.8, 1.5))
                return True
        except Exception as e:
            logger.debug(f"Like failed: {e}")
        return False

    async def comment(self, tweet_el, text: str) -> bool:
        if self.dry_run:
            logger.info(f"[DRY-RUN] comment: {text[:60]}")
            return True
        await self.dismiss_overlay()
        try:
            reply_btn = await tweet_el.query_selector(self.SEL["reply"])
            if not reply_btn:
                logger.debug("Reply button not found on tweet element")
                return False
            await reply_btn.click(timeout=5000)
            await asyncio.sleep(random.uniform(2.0, 3.0))

            # Try multiple selectors for the reply textarea (X's UI varies)
            inp = None
            for sel in [
                '[data-testid="tweetTextarea_0"]',
                'div[role="textbox"][contenteditable="true"]',
                '[contenteditable="true"]',
                '.public-DraftEditor-content',
            ]:
                try:
                    await self._page.wait_for_selector(sel, timeout=5000)
                    inp = await self._page.query_selector(sel)
                    if inp:
                        logger.debug(f"Reply input found: {sel}")
                        break
                except Exception:
                    continue

            if not inp:
                logger.error("Could not find reply textarea — skipping")
                await self._page.keyboard.press("Escape")
                return False

            await inp.click(timeout=3000)
            await self._page.keyboard.type(text, delay=random.randint(40, 80))
            await asyncio.sleep(random.uniform(0.8, 1.5))

            # Try multiple selectors for the submit button
            sub = None
            for sub_sel in [
                '[data-testid="tweetButtonInline"]',
                '[data-testid="tweetButton"]',
            ]:
                sub = await self._page.query_selector(sub_sel)
                if sub:
                    logger.debug(f"Submit button found: {sub_sel}")
                    break

            if sub:
                await sub.click(timeout=5000)
                await asyncio.sleep(2)
                return True
            else:
                logger.error("Submit button not found — pressing Enter instead")
                await self._page.keyboard.press("Enter")
                await asyncio.sleep(2)
                return True
        except Exception as e:
            logger.error(f"Comment failed: {e}")
            try:
                await self._page.keyboard.press("Escape")
            except Exception:
                pass
        return False

    async def post_tweet(self, text: str) -> bool:
        """Compose and publish a brand-new original tweet (not a reply)."""
        if self.dry_run:
            logger.info(f"[DRY-RUN] post: {text[:60]}")
            return True
        await self.dismiss_overlay()
        try:
            await self._page.goto("https://x.com/compose/tweet", wait_until="domcontentloaded")
            await self.dismiss_overlay()
            await asyncio.sleep(1.0)

            inp = None
            for sel in [
                '[data-testid="tweetTextarea_0"]',
                'div[role="textbox"][contenteditable="true"]',
                '[contenteditable="true"]',
            ]:
                try:
                    await self._page.wait_for_selector(sel, timeout=8000)
                    inp = await self._page.query_selector(sel)
                    if inp:
                        logger.debug(f"Compose textarea found: {sel}")
                        break
                except Exception:
                    continue

            if not inp:
                logger.error("Could not find compose textarea — skipping post")
                await self._page.keyboard.press("Escape")
                return False

            await inp.click(timeout=3000)
            await self._page.keyboard.type(text, delay=random.randint(40, 80))
            await asyncio.sleep(random.uniform(0.8, 1.5))

            sub = await self._page.query_selector('[data-testid="tweetButton"]')
            if sub:
                await sub.click(timeout=5000)
                await asyncio.sleep(2)
                return True
            else:
                logger.error("Post button not found — trying Ctrl+Enter instead")
                await self._page.keyboard.press("Control+Enter")
                await asyncio.sleep(2)
                return True
        except Exception as e:
            logger.error(f"Post failed: {e}")
            try:
                await self._page.keyboard.press("Escape")
            except Exception:
                pass
        return False

    async def follow_user(self, author: str) -> bool:
        """Navigate to the user's profile and click Follow there."""
        if self.dry_run:
            logger.info(f"[DRY-RUN] follow @{author}")
            return True
        try:
            await self._page.goto(
                f"https://x.com/{author}", wait_until="domcontentloaded"
            )
            await self.dismiss_overlay()

            # Wait for profile to render before looking for follow button
            try:
                await self._page.wait_for_selector(
                    '[data-testid="primaryColumn"]', timeout=8000
                )
            except Exception:
                pass
            await asyncio.sleep(1.0)
            await self.dismiss_overlay()

            # Try all known follow button selector variants
            btn = None
            selectors = [
                f'[data-testid="{author}-follow"]',
                '[data-testid="follow"]',
                f'[aria-label="Follow @{author}"]',
                f'[aria-label="Follow {author}"]',
                'button[data-testid*="-follow"]',
            ]
            for sel in selectors:
                btn = await self._page.query_selector(sel)
                if btn:
                    logger.debug(f"Follow button found via: {sel}")
                    break

            # Last resort: find any visible button whose text is exactly "Follow"
            if not btn:
                buttons = await self._page.query_selector_all('button')
                for b in buttons:
                    try:
                        txt = (await b.inner_text()).strip()
                        if txt == "Follow":
                            btn = b
                            logger.debug("Follow button found via text match")
                            break
                    except Exception:
                        continue

            if not btn:
                logger.debug(f"Follow button not found on @{author} profile")
                return False

            await btn.click(timeout=5000)
            await asyncio.sleep(random.uniform(1.0, 2.0))
            logger.info(f"Followed @{author}")
            return True
        except Exception as e:
            logger.debug(f"Follow @{author} failed: {e}")
        return False

    async def get_tweet_elements(self) -> list:
        try:
            return await self._page.query_selector_all(self.SEL["tweet"])
        except Exception:
            return []

    async def dismiss_overlay(self) -> None:
        """Close any modal/overlay that blocks clicks."""
        try:
            has_overlay = await self._page.evaluate(
                "() => { const l = document.querySelector('#layers'); return !!(l && l.children.length > 0); }"
            )
            if not has_overlay:
                return

            # Log what's in layers for debugging
            layers_text = await self._page.evaluate(
                "() => (document.querySelector('#layers')?.innerText || '').slice(0, 120)"
            )
            logger.debug(f"Overlay content: {layers_text.strip()!r}")

            # Try known close buttons first (Premium upsell, modals, etc.)
            for sel in [
                '[aria-label="Close"]',
                '[data-testid="app-bar-close"]',
                'button[data-testid="confirmationSheetCancel"]',
                'button[data-testid="sheetCancel"]',
                '[data-testid="mask"]',
            ]:
                try:
                    btn = await self._page.query_selector(sel)
                    if btn:
                        logger.debug(f"Closing overlay via {sel}")
                        await btn.click(timeout=2000)
                        await asyncio.sleep(0.5)
                        return
                except Exception:
                    pass

            # Fall back to Escape
            await self._page.keyboard.press("Escape")
            await asyncio.sleep(0.8)

            # Still there — click at a neutral position
            still = await self._page.evaluate(
                "() => { const l = document.querySelector('#layers'); return !!(l && l.children.length > 0); }"
            )
            if still:
                await self._page.mouse.click(5, 5)
                await asyncio.sleep(0.4)
        except Exception:
            pass

    async def scroll(self) -> None:
        try:
            await self._page.evaluate("window.scrollBy(0, window.innerHeight * 1.5)")
            await asyncio.sleep(1.5)
        except Exception:
            pass


# ── Comment generator (wraps ClaudeSessionProvider) ─────────────────────────
class CommentGenerator:
    def __init__(
        self,
        timeout_s: int = 30,
        dry_run: bool = False,
        model: str | None = None,
    ):
        self._dry_run = dry_run
        self._provider = None
        if not dry_run:
            mod = _load_module(
                "xeepy.ai.providers.groq_provider",
                "xeepy/ai/providers/groq_provider.py",
            )
            self._provider = mod.GroqProvider(model=model, timeout_s=timeout_s)
        self._started = False

    async def start(self) -> None:
        if self._dry_run:
            logger.info("CommentGenerator: dry-run mode — Groq skipped.")
            return
        await self._provider.start()
        self._started = True

    async def stop(self) -> None:
        if self._started:
            await self._provider.stop()
            self._started = False

    @staticmethod
    def _extract_comment(raw: str) -> Optional[str]:
        """Parse the model's JSON decision. Returns the comment string when the
        model marked the post relevant, or None to skip. Falls back to a
        conservative refusal check only if the JSON can't be parsed."""
        cleaned = raw.strip()

        # Strip a ```json ... ``` fence if the model wrapped its output
        fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL | re.IGNORECASE)
        if fence:
            cleaned = fence.group(1).strip()

        try:
            data = json.loads(cleaned)
            if isinstance(data, dict):
                if not data.get("relevant"):
                    logger.debug("Comment skipped: model marked post not relevant")
                    return None
                comment = str(data.get("comment") or "").strip()
                if not comment:
                    logger.debug("Comment skipped: relevant=true but empty comment")
                    return None
                return comment
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

        # Fallback: JSON parsing failed — treat obvious refusal phrasing as skip
        # so a malformed response can never leak a refusal onto a post.
        if re.match(r"^\s*skip\b", cleaned, re.IGNORECASE):
            logger.debug("Comment skipped: non-JSON SKIP response")
            return None
        refusal = re.compile(
            r"(not\s+(really\s+)?(my|within\s+my|in\s+my)\s+(area|field|domain|wheelhouse|expertise)"
            r"|area\s+of\s+expertise|out(?:side)?\s+(of\s+)?my\s+(expertise|area|depth)"
            r"|not\s+(my\s+)?expertise|nothing\s+(much\s+)?to\s+add|not\s+qualified"
            r"|not\s+(really\s+)?(relevant|related)|can'?t\s+(really\s+)?add\s+(anything|value|much)"
            r"|cannot\s+add\s+(anything|value|much)|no\s+(real\s+)?value\s+to\s+add"
            r"|i'?(ll|d)\s+skip|i\s+will\s+skip|i'?m\s+not\s+(sure|able|familiar)"
            r"|not\s+something\s+i\s+(can|could|would)|beyond\s+my)",
            re.IGNORECASE,
        )
        if refusal.search(cleaned):
            logger.debug(f"Comment skipped: refusal phrasing in non-JSON response: {cleaned[:80]!r}")
            return None
        # Last resort: treat the raw text as the comment (quality gates still apply)
        return cleaned or None

    async def generate(self, tweet: Tweet) -> Optional[str]:
        if self._dry_run:
            return f"[DRY-RUN comment for @{tweet.author}: {tweet.text[:40]}...]"

        prompt = (
            f'Tweet by @{tweet.author or "unknown"}:\n'
            f'"{tweet.text[:500]}"\n\n'
            "Decide whether you (per your persona) should reply, then return ONLY this JSON object:\n"
            '{"relevant": true or false, "comment": "the reply text, or empty string"}\n\n'
            "Set relevant=false (and comment to \"\") if the post is outside your expertise, "
            "off-topic, spam, or you cannot add a specific, credible reply. "
            "Set relevant=true ONLY when you can add real value; put the reply in comment. "
            "The comment must be under 160 chars, sound like a real person, no hashtags."
        )
        try:
            raw = await self._provider.generate(
                prompt, system_prompt=SYSTEM_PROMPT, json_mode=True, max_tokens=200
            )
        except Exception as e:
            logger.error(f"Comment generation error: {e}")
            return None

        if not raw:
            return None

        # --- DECISION GATE (structured) ---------------------------------------
        # The model returns an explicit boolean instead of us guessing from prose.
        # generate() returns None as its "do NOT comment" flag; only a real
        # string means "comment".
        text = self._extract_comment(raw)
        if text is None:
            return None

        # Quality gates
        # Strip numbered list prefix
        text = re.sub(r"^\d+[\.\)]\s*", "", text)
        # Strip hashtags
        text = re.sub(r"\s*#\w+", "", text).strip()
        # Strip wrapping quotes
        text = re.sub(r'^["\'""\s]+|["\'""\s]+$', "", text).strip()

        # Banned openers
        banned_openers = re.compile(
            r"^(great\s+(post|insight|take|thread|question)|amazing|awesome|love\s+this|"
            r"so\s+true|well\s+said|excellent|fantastic|wonderful|brilliant|"
            r"absolutely|certainly|indeed|fascinating|interesting\s+perspective)",
            re.IGNORECASE,
        )
        if banned_openers.match(text):
            logger.debug("Comment rejected: banned opener detected")
            return None

        # AI sanitization — reject comments that sound generated
        ai_phrases = re.compile(
            r"\b(it(?:'s| is) worth noting|it(?:'s| is) important to note|"
            r"furthermore|moreover|additionally|in conclusion|"
            r"you(?:'ve| have) (touched on|highlighted|raised|mentioned)|"
            r"as an ai|thought.provoking|i(?:'d| would) also add|"
            r"the (evolving|ever.evolving) (landscape|world)|"
            r"significant(ly)? implications|worth exploring|nuances? (here|of)|"
            r"this is a (great|fascinating|interesting)|"
            r"i (completely|totally|fully) agree)\b",
            re.IGNORECASE,
        )
        if ai_phrases.search(text):
            logger.debug("Comment rejected: AI phrasing detected")
            return None

        # Length cap — 2 lines / 160 chars normally, hard cap at 320
        if len(text) > 320:
            return None  # too long, skip rather than truncate badly
        if len(text) > 160:
            logger.debug("Comment over 160 chars — allowing (extended comment)")

        return text if len(text) > 5 else None

    async def generate_post(self, recent_posts: list[str]) -> Optional[str]:
        """Generate a brand-new standalone tweet (not a reply)."""
        if self._dry_run:
            return "[DRY-RUN original post]"

        angle = random.choice(POST_ANGLES)
        avoid = "\n".join(f"- {p}" for p in recent_posts[-5:]) or "(none yet today)"
        prompt = (
            f"Write one short, standalone original tweet about: {angle}.\n\n"
            f"Don't repeat the ideas or phrasing of these recent posts:\n{avoid}\n\n"
            "Max 200 characters. Sound like a real engineer posting a quick thought, not an AI."
        )
        try:
            raw = await self._provider.generate(prompt, system_prompt=POST_SYSTEM_PROMPT)
        except Exception as e:
            logger.error(f"Post generation error: {e}")
            return None

        if not raw:
            return None

        text = raw.strip()
        text = re.sub(r"^\d+[\.\)]\s*", "", text)
        text = re.sub(r"\s*#\w+", "", text).strip()
        text = re.sub(r'^["\'""\s]+|["\'""\s]+$', "", text).strip()

        if len(text) > 280:
            return None  # too long for a tweet, skip rather than truncate badly

        return text if len(text) > 5 else None


# ── randomized daily post-target state ───────────────────────────────────────
def _load_post_state() -> dict:
    if POST_TARGET_FILE.exists():
        try:
            return json.loads(POST_TARGET_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_post_state(state: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    POST_TARGET_FILE.write_text(json.dumps(state, indent=2))


def _pick_daily_post_target() -> int:
    """Randomized daily original-post count within the configured range
    (POSTS_PER_DAY_MIN..MAX), with an occasional quieter day for natural
    day-to-day variance. Always clamped to SAFETY_MAX_POSTS_DAY."""
    lo = max(0, POSTS_PER_DAY_MIN)
    hi = max(lo, POSTS_PER_DAY_MAX)
    if random.random() < 0.85:
        target = random.randint(lo, hi)
    else:
        target = max(0, lo - 1)  # occasional quiet day
    return min(target, SAFETY_MAX_POSTS_DAY)


def _schedule_post_times(target: int) -> list[float]:
    """Spread `target` posts across the remainder of today's active-hours window, with jitter."""
    now = datetime.now()
    start = now.replace(hour=ACTIVE_START, minute=0, second=0, microsecond=0)
    end = now.replace(hour=ACTIVE_END, minute=0, second=0, microsecond=0)
    if now >= end or target <= 0:
        return []
    window_start = max(now, start)
    window_seconds = (end - window_start).total_seconds()
    if window_seconds <= 0:
        return []
    slot = window_seconds / target
    due_times = []
    for i in range(target):
        base = window_start.timestamp() + slot * i
        jitter = random.uniform(0, slot * 0.8)
        due_times.append(base + jitter)
    return sorted(due_times)


def get_or_create_post_state() -> dict:
    """Load today's post-target state, rolling over to a fresh random target on a new calendar day."""
    today = date.today().isoformat()
    state = _load_post_state()
    if state.get("date") != today:
        target = _pick_daily_post_target()
        state = {
            "date": today,
            "target": target,
            "posted_count": 0,
            "recent_posts": state.get("recent_posts", [])[-5:],
            "due_times": _schedule_post_times(target),
        }
        _save_post_state(state)
        logger.info(f"New day — original-post target for {today}: {target}")
    return state


def _next_due_post_index(state: dict) -> Optional[int]:
    """Return the index of the next due, not-yet-posted slot, or None if nothing is due yet."""
    due_times = state.get("due_times", [])
    posted_count = state.get("posted_count", 0)
    if posted_count >= len(due_times):
        return None
    if due_times[posted_count] <= time.time():
        return posted_count
    return None


# ── Main orchestrator ─────────────────────────────────────────────────────────
@dataclass
class BotConfig:
    max_comments: int = field(default_factory=lambda: _env_int("MAX_COMMENTS", 500))
    max_likes: int = field(default_factory=lambda: _env_int("MAX_LIKES", 300))
    max_follows: int = field(default_factory=lambda: _env_int("MAX_FOLLOWS", 150))
    comment_delay_min: int = field(default_factory=lambda: _env_int("COMMENT_DELAY_MIN", 90))
    comment_delay_max: int = field(default_factory=lambda: _env_int("COMMENT_DELAY_MAX", 150))
    like_delay_min: int = field(default_factory=lambda: _env_int("LIKE_DELAY_MIN", 3))
    like_delay_max: int = field(default_factory=lambda: _env_int("LIKE_DELAY_MAX", 8))
    follow_delay_min: int = field(default_factory=lambda: _env_int("FOLLOW_DELAY_MIN", 5))
    follow_delay_max: int = field(default_factory=lambda: _env_int("FOLLOW_DELAY_MAX", 12))
    groq_timeout_s: int = field(default_factory=lambda: _env_int("GROQ_TIMEOUT_S", 30))
    groq_model: str = field(default_factory=lambda: os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"))
    dry_run: bool = False
    headless: bool = True
    fast_mode: bool = False           # skip human delays (for testing only)


class GrowthBot:
    def __init__(self, config: BotConfig):
        self.config = config
        self.stats = BotStats()
        self.twitter: Optional[TwitterBot] = None
        self.claude: Optional[CommentGenerator] = None
        self.safety = None
        self._action_limiter = None
        self._stop = False

    async def run(self) -> None:
        cfg = self.config
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        logger.remove()  # drop default stderr sink — avoids duplicate lines when stderr is redirected
        logger.add(str(LOG_DIR / "growth_bot.log"), rotation="10 MB", retention="7 days", level="DEBUG")

        logger.info(
            f"Growth bot starting — target: {cfg.max_comments} comments, "
            f"{cfg.max_likes} likes, {cfg.max_follows} follows"
        )

        # Start Twitter browser
        self.twitter = TwitterBot(
            session_file=SESSION_FILE,
            headless=cfg.headless,
            dry_run=cfg.dry_run,
        )
        await self.twitter.start()

        # Start comment generator (Groq API)
        self.claude = CommentGenerator(
            timeout_s=cfg.groq_timeout_s,
            dry_run=cfg.dry_run,
            model=cfg.groq_model,
        )
        await self.claude.start()

        # Safety monitor — authoritative, SQLite-persisted daily cap for original posts
        # (independent of the JSON post-target state, so a bug in the target logic
        # can never post more than SAFETY_MAX_POSTS_DAY in a calendar day).
        if not cfg.dry_run:
            safety_mod = _load_module("xeepy.safety_monitor", "xeepy/safety_monitor.py")
            self.safety = safety_mod.SafetyMonitor()
            rl_mod = _load_module("xeepy.core.rate_limiter", "xeepy/core/rate_limiter.py")
            self._action_limiter = rl_mod.ActionRateLimiter()

        try:
            await self._main_loop()
        finally:
            if self.twitter:
                await self.twitter.stop()
            if self.claude:
                await self.claude.stop()
            if self.safety:
                self.safety.close()

        logger.info(f"Bot finished. {self.stats.report()}")

    async def _main_loop(self) -> None:
        cfg = self.config
        cycle = 0

        while not self._stop:
            if not self._in_active_hours():
                logger.info(f"Outside active hours ({ACTIVE_START}–{ACTIVE_END}). Sleeping 5m.")
                await asyncio.sleep(300)
                continue

            if self._daily_targets_met():
                logger.info(f"Daily targets met. {self.stats.report()}")
                break

            cycle += 1
            logger.info(f"--- Cycle {cycle} | {self.stats.report()}")

            authors_to_follow: list[str] = []

            # Alternate between home feed and keyword searches.
            # IMPORTANT: element handles are only valid until the next page
            # navigation, so each batch MUST be fully processed (liked/commented)
            # before searching the next keyword — otherwise the prior batch's
            # handles are destroyed by page.goto and every action fails with
            # "Cannot find context with specified id".
            if cycle % 3 == 0:
                tweets = await self.twitter.get_home_tweets(limit=20)
                if tweets:
                    await self._process_batch(cfg, cycle, tweets, authors_to_follow)
                else:
                    logger.info("No new tweets found this cycle — waiting 30s")
                    await asyncio.sleep(30)
                    continue
            else:
                # Search 2 distinct keywords per cycle to widen the pool the
                # scorer picks from — read-only page loads, no extra writes,
                # so this doesn't raise ban risk.
                kws = random.sample(KEYWORDS, k=min(2, len(KEYWORDS)))
                got_any = False
                for i, kw in enumerate(kws):
                    if self._stop or self._daily_targets_met():
                        break
                    logger.info(f"Searching: '{kw}'")
                    batch = await self.twitter.search_tweets(kw, limit=20)
                    if batch:
                        got_any = True
                        await self._process_batch(cfg, cycle, batch, authors_to_follow)
                    if i < len(kws) - 1:
                        await asyncio.sleep(random.uniform(2, 4))
                if not got_any:
                    logger.info("No new tweets found this cycle — waiting 30s")
                    await asyncio.sleep(30)
                    continue

            # FOLLOW — visit each profile page after tweet loop (element handles no longer needed)
            for author in authors_to_follow:
                if self._stop or self.stats.follows >= cfg.max_follows:
                    break
                if not self._in_active_hours():
                    break
                ok = await self.twitter.follow_user(author)
                if ok:
                    self.stats.follows += 1
                    await asyncio.sleep(random.uniform(cfg.follow_delay_min, cfg.follow_delay_max))

            # ORIGINAL POST — randomized daily count, spread across active hours
            await self._maybe_post_original(cfg)

            # Scroll to load more tweets, then wait before next cycle
            await self.twitter.scroll()
            if not self._daily_targets_met() and not self._stop:
                await asyncio.sleep(random.uniform(15, 30))

    async def _process_batch(
        self,
        cfg: BotConfig,
        cycle: int,
        tweets: list[Tweet],
        authors_to_follow: list[str],
    ) -> None:
        """Like/comment on one freshly-collected batch. Handles are valid only
        until the next navigation, so this must run before any further page.goto."""
        scores = [score_tweet(t.text)[0] for t in tweets if t.text]
        accepted = sum(1 for s in scores if s >= COMMENT_SCORE_THRESHOLD)
        logger.info(
            f"Cycle {cycle} batch: {len(scores)} tweets scored, {accepted} accepted "
            f"(>={COMMENT_SCORE_THRESHOLD}), {len(scores) - accepted} rejected"
        )

        random.shuffle(tweets)

        for tweet in tweets:
            if self._stop or self._daily_targets_met():
                break
            if not self._in_active_hours():
                break

            el = tweet.el  # element handle — valid only for the current page

            # Skip own posts
            if MY_HANDLE and tweet.author.lower() == MY_HANDLE:
                logger.debug(f"Skipping own post by @{tweet.author}")
                continue

            # Skip non-English posts entirely
            if not is_english(tweet.text):
                logger.debug(f"Skipping non-English tweet by @{tweet.author}")
                self.stats.skipped += 1
                continue

            # LIKE
            if self.stats.likes < cfg.max_likes and el:
                ok = await self.twitter.like(el)
                if ok:
                    self.stats.likes += 1
                    logger.info(f"Liked @{tweet.author}: {tweet.text[:50]}")
                    await asyncio.sleep(random.uniform(cfg.like_delay_min, cfg.like_delay_max))

            # COMMENT (AI-generated) — only for tweets scoring >= COMMENT_SCORE_THRESHOLD
            if self.stats.comments < cfg.max_comments and tweet.text:
                tweet_score, score_reasons = score_tweet(tweet.text)
                if tweet_score < COMMENT_SCORE_THRESHOLD:
                    logger.debug(
                        f"Tweet skipped (score={tweet_score}, reasons={score_reasons}): "
                        f"@{tweet.author}: {tweet.text[:50]}"
                    )
                    self.stats.skipped += 1
                    continue
                logger.debug(f"Tweet accepted (score={tweet_score}, reasons={score_reasons})")
                comment = await self.claude.generate(tweet)
                if comment and el:
                    ok = await self.twitter.comment(el, comment)
                    if ok:
                        self.stats.comments += 1
                        logger.info(
                            f"Commented on @{tweet.author}: \"{comment[:60]}...\""
                        )
                        # Queue follow only for users we actually commented on
                        if self.stats.follows < cfg.max_follows and tweet.author:
                            authors_to_follow.append(tweet.author)
                        if not cfg.fast_mode:
                            delay = random.uniform(cfg.comment_delay_min, cfg.comment_delay_max)
                            logger.debug(f"Waiting {delay:.0f}s before next comment...")
                            await asyncio.sleep(delay)
                    else:
                        self.stats.errors += 1
                elif comment is None:
                    self.stats.skipped += 1
                    logger.debug("Claude skipped this tweet.")

    async def _maybe_post_original(self, cfg: BotConfig) -> None:
        """Post an original (non-reply) tweet if today's randomized schedule has one due."""
        state = get_or_create_post_state()
        idx = _next_due_post_index(state)
        if idx is None:
            return

        # ActionRateLimiter is a secondary guard against clustering; SafetyMonitor.record
        # below is the authoritative hard daily cap.
        if self._action_limiter:
            await self._action_limiter.acquire("post")

        if self.safety and not await self.safety.record("post", note=f"slot {idx + 1}/{state['target']}"):
            return  # daily cap or active cooldown — hard safety ceiling wins

        text = await self.claude.generate_post(state.get("recent_posts", []))
        if not text:
            logger.debug("Original post generation skipped/failed this slot.")
            return

        ok = await self.twitter.post_tweet(text)
        if self.safety:
            await self.safety.record_outcome("post", None, ok)

        if ok:
            self.stats.posts += 1
            state["posted_count"] = idx + 1
            recent = state.get("recent_posts", [])
            recent.append(text)
            state["recent_posts"] = recent[-5:]
            _save_post_state(state)
            logger.info(
                f"Posted original tweet ({state['posted_count']}/{state['target']}): "
                f"\"{text[:60]}...\""
            )
            await asyncio.sleep(random.uniform(cfg.comment_delay_min, cfg.comment_delay_max))
        else:
            self.stats.errors += 1

    def _in_active_hours(self) -> bool:
        return True  # runs 24/7 until stopped

    def _daily_targets_met(self) -> bool:
        cfg = self.config
        return (
            self.stats.comments >= cfg.max_comments
            and self.stats.likes >= cfg.max_likes
            and self.stats.follows >= cfg.max_follows
        )

    def stop(self) -> None:
        self._stop = True


# ── CLI ───────────────────────────────────────────────────────────────────────
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="XActions Growth Bot")
    p.add_argument("--setup-claude", action="store_true",
                   help="(Deprecated — Groq API is used instead)")
    p.add_argument("--dry-run", action="store_true",
                   help="Run without posting anything")
    p.add_argument("--headless", action="store_true", default=HEADLESS,
                   help="Run browsers in headless mode (default: true)")
    p.add_argument("--no-headless", dest="headless", action="store_false")
    # These default to None so the env-backed BotConfig defaults apply unless
    # a value is explicitly passed on the command line (CLI overrides env).
    p.add_argument("--comments", type=int, default=None,
                   help="Max AI comments per run (default: MAX_COMMENTS env or 500)")
    p.add_argument("--likes", type=int, default=None,
                   help="Max likes per run (default: MAX_LIKES env or 300)")
    p.add_argument("--follows", type=int, default=None,
                   help="Max follows per run (default: MAX_FOLLOWS env or 150)")
    p.add_argument("--model", default=None,
                   help="Groq model (default: GROQ_MODEL env or llama-3.3-70b-versatile)")
    p.add_argument("--comment-delay", type=int, default=None,
                   help="Min seconds between comments (default: COMMENT_DELAY_MIN env or 90)")
    p.add_argument("--fast", action="store_true",
                   help="Skip human delays — for testing only, don't use for real runs")
    return p.parse_args()


async def main() -> None:
    args = parse_args()

    if args.setup_claude:
        logger.warning("--setup-claude is no longer needed. The bot now uses Groq API.")
        logger.info("Get a free Groq API key at https://console.groq.com")
        logger.info("Then add GROQ_API_KEY=gsk_... to your .env file and run: python growth_bot.py")
        return

    # Start from env-backed defaults; apply only the CLI flags that were provided.
    overrides: dict = {
        "dry_run": args.dry_run,
        "headless": args.headless,
        "fast_mode": args.fast,
    }
    if args.comments is not None:
        overrides["max_comments"] = args.comments
    if args.likes is not None:
        overrides["max_likes"] = args.likes
    if args.follows is not None:
        overrides["max_follows"] = args.follows
    if args.model is not None:
        overrides["groq_model"] = args.model
    if args.comment_delay is not None:
        overrides["comment_delay_min"] = args.comment_delay
        overrides["comment_delay_max"] = args.comment_delay + 60

    config = BotConfig(**overrides)

    bot = GrowthBot(config)
    try:
        await bot.run()
    except KeyboardInterrupt:
        logger.info("Interrupted — stopping gracefully.")
        bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
