"""
Activity feed — structured JSONL event log powering the live dashboard.

The growth bot calls emit() at every meaningful action (like, comment, follow,
post, skip, error, status change). Each call appends one JSON line to
data/activity.jsonl, which dashboard.py tails and streams to the browser
over Server-Sent Events.

Design constraints:
- emit() must NEVER raise — a broken dashboard must not break the bot.
- No third-party dependencies — importable standalone by dashboard.py.
- The file self-trims so it can run 24/7 without growing unbounded.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

_ROOT = Path(__file__).parent.parent  # python/
_MAX_BYTES = 2_000_000   # trim when the feed file exceeds ~2 MB
_KEEP_LINES = 2_000      # ...down to the most recent N events


def feed_path() -> Path:
    """Resolve the activity feed file (override via XEEPY_ACTIVITY_FILE)."""
    raw = (os.environ.get("XEEPY_ACTIVITY_FILE") or "").strip()
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else (_ROOT / p)
    data_dir = Path(os.environ.get("XEEPY_DATA_DIR", _ROOT / "data"))
    return data_dir / "activity.jsonl"


def _trim(path: Path) -> None:
    """Keep the file bounded: drop everything but the newest _KEEP_LINES."""
    try:
        if path.stat().st_size <= _MAX_BYTES:
            return
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        path.write_text("\n".join(lines[-_KEEP_LINES:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def emit(event: str, **data) -> None:
    """Append one structured event. Swallows every error by design."""
    try:
        path = feed_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"t": round(time.time(), 3), "event": event, **data}
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        _trim(path)
    except Exception:
        pass


def read_recent(limit: int = 500) -> list[dict]:
    """Return the newest `limit` events, oldest first (for dashboard backlog)."""
    path = feed_path()
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out: list[dict] = []
    for ln in lines[-limit:]:
        try:
            rec = json.loads(ln)
            if isinstance(rec, dict):
                out.append(rec)
        except json.JSONDecodeError:
            continue
    return out
