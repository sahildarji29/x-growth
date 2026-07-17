<h1 align="center">🐦 XActions Growth Bot</h1>
<h3 align="center">A configurable, persona-driven X/Twitter growth agent — no paid API needed</h3>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.10+"/>
  <img src="https://img.shields.io/badge/Playwright-Browser_Automation-2EAD33?style=for-the-badge&logo=playwright&logoColor=white" alt="Playwright"/>
  <img src="https://img.shields.io/badge/Groq-Free_LLM-FF6F00?style=for-the-badge" alt="Groq"/>
</p>

The Growth Bot runs 24/7 on your machine, using **your own X session** and a **Groq
LLM** (free tier) to engage authentically in your voice: it likes relevant posts,
writes on-topic comments, follows people it engages with, and posts a few original
tweets per day — all driven by **your persona and config**, with safety limits to
protect your account.

Everything that makes it *you* — persona, target topics, volumes, pacing — lives in
`.env` and a couple of editable text files. **No code changes needed.** Download it,
fill in your details, and run.

> ⚠️ **Use responsibly.** Automating engagement can violate X's Terms of Service and
> risk your account. Keep volumes low, pacing human, and treat every default here as a
> ceiling, not a target. You are responsible for how you use this.

---

## Table of Contents

- [How it works](#how-it-works)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
  - [1. Secrets & identity (`.env`)](#1-secrets--identity-env)
  - [2. Your persona](#2-your-persona)
  - [3. Targeting & scoring](#3-targeting--scoring)
  - [4. Volumes, posts & pacing](#4-volumes-posts--pacing)
- [First run (login)](#first-run-login)
- [Usage](#usage)
- [Monitoring](#monitoring)
- [Safety](#safety)
- [Running multiple accounts](#running-multiple-accounts)
- [Troubleshooting](#troubleshooting)
- [Configuration reference](#configuration-reference)

---

## How it works

Each cycle the bot:

1. **Searches** your `TARGET_KEYWORDS` (and periodically your home feed) for fresh tweets.
2. **Filters** them — skips your own posts and any non-English tweets.
3. **Likes** relevant tweets (English, not your own).
4. **Scores** each tweet against your keyword tiers. Only tweets scoring at or above
   `COMMENT_SCORE_THRESHOLD` are sent to the LLM.
5. **Decides & comments** — the LLM returns a strict JSON decision
   (`{"relevant": true/false, "comment": "..."}`). Off-topic or low-value posts are
   skipped; only genuine, in-voice comments are posted. **No refusal text ever gets posted.**
6. **Follows** only the people it actually commented on.
7. **Posts** a randomized 2–3 original tweets per day, spread across your active hours.

All writes are paced with human-like delays and capped by persistent daily safety limits.

---

## Prerequisites

- **Python 3.10+**
- A **Groq API key** — free at <https://console.groq.com> (format `gsk_...`)
- Your **X / Twitter account** credentials (used once for login)
- A machine that can stay on (the bot runs continuously)

---

## Installation

```bash
# 1. Get the code
git clone <your-repo-url>
cd XActions/python

# 2. (Recommended) create a virtual environment
python -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r xeepy/requirements.txt

# 4. Install the Playwright browser
python -m playwright install chromium
```

---

## Configuration

Everything is configured through `.env` + a few text files. Nothing is shared between
users — each person has their own copy.

### 1. Secrets & identity (`.env`)

```bash
cp .env.example .env
```

Open `.env` and fill in the essentials:

| Key | What it is |
|---|---|
| `XEEPY_USERNAME` | your X handle **without** the `@` (used to skip your own posts) |
| `XEEPY_PASSWORD` | your X password (used only for the first login) |
| `GROQ_API_KEY` | your free key from console.groq.com |

Every other value has a sensible default and is documented inline in `.env.example`.

### 2. Your persona

The persona is what makes replies sound like **you**. Copy the templates and edit them:

```bash
cp config/persona_comment.example.md  config/persona_comment.md
cp config/persona_post.example.md     config/persona_post.md
cp config/post_angles.example.txt     config/post_angles.txt
```

Then edit those three files:

- **`config/persona_comment.md`** — who you are, your audience, positioning, voice, and
  when to skip. Write in first person (“You are &lt;Your Name&gt;, a …”).
  ⚠️ Keep the `OUTPUT — STRICT JSON` section at the bottom **unchanged** — the bot relies on it.
- **`config/persona_post.md`** — same voice, but for standalone original tweets.
- **`config/post_angles.txt`** — one topic angle per line; the bot picks one at random
  for each original post.

Your real persona files are **gitignored**, so they stay private and never get committed.

### 3. Targeting & scoring

Tune these in `.env` to your niche:

```ini
# What the bot searches for
TARGET_KEYWORDS=your topic,another topic,a third topic

# Relevance scoring — a tweet only gets a comment when its score >= threshold
COMMENT_SCORE_THRESHOLD=6
SCORE_HIGH=your core topics        # +10 each (one match clears the threshold)
SCORE_MEDIUM=adjacent topics       # +6 each (one match just clears it)
SCORE_NEGATIVE=topics to avoid     # -10 each (sinks the score)
```

Likes are **not** gated by score (any relevant English tweet may be liked); only
**comments** require the score threshold. This keeps your replies tightly on-topic.

### 4. Volumes, posts & pacing

Start conservative and increase slowly over days/weeks:

```ini
# Per-run engagement targets (bot stops once all are hit)
MAX_COMMENTS=500
MAX_LIKES=300
MAX_FOLLOWS=150

# Original tweets per day (randomized between MIN and MAX; hard ceiling below)
POSTS_PER_DAY_MIN=2
POSTS_PER_DAY_MAX=3
SAFETY_MAX_POSTS_DAY=3

# Human-like delays (seconds) — lower is riskier
COMMENT_DELAY_MIN=90
COMMENT_DELAY_MAX=150
LIKE_DELAY_MIN=3
LIKE_DELAY_MAX=8
FOLLOW_DELAY_MIN=5
FOLLOW_DELAY_MAX=12
```

---

## First run (login)

The bot drives a real browser using a saved session. On the very first run it needs to
log in and save that session to `data/session.json`.

Run once with the browser **visible** so you can complete any login/2FA prompts:

```bash
python growth_bot.py --no-headless --dry-run
```

`--dry-run` generates comments/posts but **doesn't actually post them** — perfect for
verifying your persona sounds right and login works. Once `data/session.json` exists,
future runs reuse it and can run headless.

---

## Usage

```bash
# Normal run — uses all values from .env
python growth_bot.py

# One-off overrides (CLI wins over .env)
python growth_bot.py --comments 100 --likes 80 --follows 30
python growth_bot.py --dry-run            # generate but don't post (test persona)
python growth_bot.py --no-headless        # watch the browser
python growth_bot.py --model llama-3.1-8b-instant   # faster, cheaper model
```

**Run it continuously in the background** (survives terminal/SSH close):

```bash
setsid nohup python growth_bot.py >> logs/growth_bot.log 2>&1 < /dev/null &
```

**Stop it:**

```bash
pkill -f "python growth_bot.py"
```

Progress (likes/comments/follows/posts) is preserved sensibly across restarts — the
daily original-post schedule persists in `data/post_target.json`, so restarting mid-day
won’t double-post or re-roll your daily count.

---

## Monitoring

```bash
# Live log
tail -f logs/growth_bot.log

# Today's safety usage (likes/comments/follows/posts vs. caps + cooldown status)
python -m xeepy.safety_monitor --status
```

Useful log lines:
- `Cycle N batch: X tweets scored, Y accepted (>=6), Z rejected` — how selective scoring is.
- `Commented on @user: "..."` — a posted comment.
- `Comment skipped: model marked post not relevant` — the relevance gate working.
- `Posted original tweet (n/target): "..."` — an original post going out.

---

## Safety

The bot has several layers to protect your account:

- **Relevance gate** — comments only on posts it can genuinely add value to.
- **Structured skip decision** — the LLM returns an explicit relevant/skip flag, so a
  refusal or “not my area” reply can never be posted.
- **Follow discipline** — follows only accounts it actually commented on.
- **Human-like pacing** — randomized delays between every action.
- **Persistent daily hard caps** (`SAFETY_MAX_*_DAY`) enforced via SQLite, surviving
  restarts, plus an automatic **cooldown** after any `429/403` from X.

Keep the defaults low. The safe numbers are intentionally conservative.

---

## Running multiple accounts

Give each account its own directory (or its own `.env`, data dir, and persona files):

```ini
# account-b/.env
XEEPY_USERNAME=account_b
XEEPY_SESSION_FILE=./data-b/session.json
XEEPY_DATA_DIR=./data-b
SAFETY_DB_PATH=./data-b/xeepy_safety.db
SAFETY_LOCK_FILE=/tmp/xeepy-b.lock
PERSONA_COMMENT_FILE=./config-b/persona_comment.md
PERSONA_POST_FILE=./config-b/persona_post.md
```

Run each from its own directory so the paths resolve independently.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `GROQ_API_KEY not set` | Add your `gsk_...` key to `.env`. |
| Login/2FA needed | Run `python growth_bot.py --no-headless` and complete it once; `data/session.json` is then reused. |
| `no tweets loaded` warnings | Transient — X was slow or the search was empty; the next cycle retries automatically. |
| `Groq rate limit hit … trying next fallback` | Normal — the bot auto-falls back across models; no action needed. |
| Comments seem too rare | Lower `COMMENT_SCORE_THRESHOLD`, or broaden `SCORE_HIGH` / `SCORE_MEDIUM` / `TARGET_KEYWORDS`. |
| Too many original posts | Lower `POSTS_PER_DAY_MIN/MAX` and `SAFETY_MAX_POSTS_DAY`. |
| `playwright … Executable doesn't exist` | Run `python -m playwright install chromium`. |

---

## Configuration reference

The single source of truth is **`.env.example`** — every setting is documented inline
there, grouped into: identity/auth, AI provider, persona, targeting, engagement volume,
original posts, pacing, safety caps, and browser/storage/logging.

See also **`GROWTH_BOT_SETUP.md`** for the condensed step-by-step setup checklist.

---

<p align="center"><sub>The previous, full toolkit README is preserved as <a href="./README.old.md">README.old.md</a>.</sub></p>
