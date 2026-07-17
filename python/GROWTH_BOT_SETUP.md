# Growth Bot — Per-User Setup

Each teammate runs their own copy against their own X account. Nothing is shared
between accounts — every user has their own `.env`, persona files, and session.

## 1. Install

```bash
cd python
pip install -r xeepy/requirements.txt
python -m playwright install chromium
```

## 2. Configure `.env`

```bash
cp .env.example .env
```

Then edit `.env` and fill in **your** values. The essentials:

| Key | What it is |
|---|---|
| `XEEPY_USERNAME` | your X handle (without @) — used to skip your own posts |
| `XEEPY_PASSWORD` | your X password (first login only) |
| `GROQ_API_KEY` | free key from https://console.groq.com |
| `PERSONA_COMMENT_FILE` | path to your comment persona (see step 3) |
| `PERSONA_POST_FILE` | path to your original-post persona |
| `TARGET_KEYWORDS` | search terms for tweets you want to engage with |
| `SCORE_HIGH` / `SCORE_MEDIUM` / `SCORE_NEGATIVE` | relevance tiers for your niche |

Everything is documented inline in `.env.example`.

## 3. Write your persona

Copy the committed **example** templates to your own files and edit them to
describe **you** (the real files are gitignored, so your persona stays private):

```bash
cp config/persona_comment.example.md  config/persona_comment.md
cp config/persona_post.example.md     config/persona_post.md
cp config/post_angles.example.txt     config/post_angles.txt
# then edit those three files
```

The persona controls voice, audience, positioning, and when to skip. Write it in
first person ("You are <Your Name>, a ..."). Keep the `OUTPUT — STRICT JSON`
section at the bottom of the comment persona unchanged — the bot relies on it.

## 4. Tune targeting to your niche

- `TARGET_KEYWORDS` — what the bot searches for.
- `SCORE_HIGH` (+10) — your core topics; one match clears the comment threshold.
- `SCORE_MEDIUM` (+6) — adjacent topics; one match just clears the threshold.
- `SCORE_NEGATIVE` (−10) — topics to never engage with.
- `COMMENT_SCORE_THRESHOLD` (default 6) — raise it to be stricter.

A tweet only gets a comment when its score ≥ threshold. Likes are not gated by
score. This keeps replies on-topic and protects your reputation.

## 5. Set safe volumes

Start conservative and increase slowly:

```
MAX_COMMENTS=... MAX_LIKES=... MAX_FOLLOWS=...   # per-run targets
POSTS_PER_DAY_MIN=2  POSTS_PER_DAY_MAX=3          # original tweets/day (randomized)
SAFETY_MAX_POSTS_DAY=3                            # hard daily ceiling
COMMENT_DELAY_MIN=90 COMMENT_DELAY_MAX=150        # seconds between comments
```

## 6. Run

```bash
python growth_bot.py
# CLI flags override .env when you want a one-off, e.g.:
python growth_bot.py --comments 100 --likes 80 --follows 30
python growth_bot.py --dry-run          # generate but don't post (test persona)
```

Check daily safety usage anytime:

```bash
python -m xeepy.safety_monitor --status
```

## Notes

- The bot only comments on posts it can add genuine value to; it returns an
  explicit relevant/skip decision from the model, so off-topic posts are skipped.
- It follows only accounts it actually commented on.
- Logs: `logs/growth_bot.log`. Daily post schedule/state: `data/post_target.json`.
