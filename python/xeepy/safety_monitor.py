"""
Safety Monitor

Tracks daily action counts across all modules, enforces hard caps,
logs every action, and provides a --status CLI flag.

Usage:
    monitor = SafetyMonitor()

    # Record an action (returns False if cap would be exceeded)
    allowed = await monitor.record("like", target="@user/tweet-url")
    if not allowed:
        return  # skip — daily cap hit

    # Trigger global cooldown (e.g. on 429 / 403)
    await monitor.trigger_cooldown("429 from like endpoint")

    # CLI status
    python -m xeepy.safety_monitor --status
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
import sys
import time
from contextlib import contextmanager
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from loguru import logger


# ---------------------------------------------------------------------------
# Hard caps — configurable via .env, these are the enforced maximums
# ---------------------------------------------------------------------------

def _int_env(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (ValueError, TypeError):
        return default


DAILY_CAPS: dict[str, int] = {
    "like":    _int_env("SAFETY_MAX_LIKES_DAY",    20),
    "comment": _int_env("SAFETY_MAX_COMMENTS_DAY",  8),
    "follow":  _int_env("SAFETY_MAX_FOLLOWS_DAY",  15),
    "unfollow":_int_env("SAFETY_MAX_UNFOLLOWS_DAY", 15),
    "post":    _int_env("SAFETY_MAX_POSTS_DAY",      3),
    "retweet": _int_env("SAFETY_MAX_RETWEETS_DAY",  10),
}

# Warn when this fraction of the daily cap is reached (e.g. 0.8 = 80%)
WARNING_THRESHOLD = float(os.environ.get("SAFETY_WARNING_THRESHOLD", "0.8"))

# How long (seconds) to pause ALL actions after a 429/403
COOLDOWN_SECONDS = _int_env("SAFETY_COOLDOWN_SECONDS", 7200)  # 2 hours

DB_PATH = Path(os.environ.get("SAFETY_DB_PATH", "xeepy_safety.db"))

LOCK_FILE = Path(os.environ.get("SAFETY_LOCK_FILE", "/tmp/xeepy.lock"))


# ---------------------------------------------------------------------------
# Lock file — prevent multiple simultaneous instances
# ---------------------------------------------------------------------------

class SingleInstanceGuard:
    """File-based lock that prevents running more than one instance."""

    def __init__(self, lock_path: Path = LOCK_FILE):
        self._path = lock_path
        self._acquired = False

    def acquire(self) -> bool:
        """Return True if lock acquired, False if another instance is running."""
        if self._path.exists():
            try:
                pid = int(self._path.read_text().strip())
                # Check if the PID is still alive
                os.kill(pid, 0)
                logger.error(
                    f"Another xeepy instance is already running (PID {pid}). "
                    "Exiting to prevent duplicate actions."
                )
                return False
            except (ValueError, ProcessLookupError):
                # Stale lock — process is dead
                self._path.unlink(missing_ok=True)

        self._path.write_text(str(os.getpid()))
        self._acquired = True
        logger.debug(f"Lock acquired: {self._path}")
        return True

    def release(self) -> None:
        if self._acquired and self._path.exists():
            try:
                self._path.unlink()
            except OSError:
                pass
        self._acquired = False

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError("Could not acquire single-instance lock")
        return self

    def __exit__(self, *_):
        self.release()


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

class _SafetyDB:
    SCHEMA_VERSION = 2

    def __init__(self, db_path: Path = DB_PATH):
        self._path = db_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._init()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    @contextmanager
    def tx(self):
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def _init(self):
        with self.tx() as c:
            # Per-day action counter (one row per action_type per calendar date)
            c.execute("""
                CREATE TABLE IF NOT EXISTS daily_action_counts (
                    action_type TEXT NOT NULL,
                    action_date TEXT NOT NULL,
                    count       INTEGER DEFAULT 0,
                    PRIMARY KEY (action_type, action_date)
                )
            """)

            # Full action log
            c.execute("""
                CREATE TABLE IF NOT EXISTS action_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    action_type TEXT    NOT NULL,
                    target      TEXT,
                    outcome     TEXT    NOT NULL,
                    note        TEXT,
                    ts          REAL    NOT NULL
                )
            """)
            c.execute("""
                CREATE INDEX IF NOT EXISTS idx_action_log_ts
                ON action_log(ts)
            """)
            c.execute("""
                CREATE INDEX IF NOT EXISTS idx_action_log_type_date
                ON action_log(action_type, ts)
            """)

            # Cooldown state (single row, keyed by name 'global')
            c.execute("""
                CREATE TABLE IF NOT EXISTS cooldown_state (
                    name        TEXT PRIMARY KEY,
                    until_ts    REAL NOT NULL,
                    reason      TEXT,
                    triggered_at REAL
                )
            """)

            # Schema version
            c.execute("""
                CREATE TABLE IF NOT EXISTS safety_schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT DEFAULT (datetime('now'))
                )
            """)
            row = c.execute(
                "SELECT MAX(version) FROM safety_schema_version"
            ).fetchone()
            if row[0] is None or row[0] < self.SCHEMA_VERSION:
                c.execute(
                    "INSERT OR REPLACE INTO safety_schema_version(version) VALUES (?)",
                    (self.SCHEMA_VERSION,),
                )

    # --- Counts ---

    def get_count(self, action_type: str, for_date: str) -> int:
        row = self.conn.execute(
            "SELECT count FROM daily_action_counts WHERE action_type=? AND action_date=?",
            (action_type, for_date),
        ).fetchone()
        return row["count"] if row else 0

    def increment(self, action_type: str, for_date: str) -> int:
        with self.tx() as c:
            c.execute(
                """INSERT INTO daily_action_counts(action_type, action_date, count)
                   VALUES (?, ?, 1)
                   ON CONFLICT(action_type, action_date)
                   DO UPDATE SET count = count + 1""",
                (action_type, for_date),
            )
        return self.get_count(action_type, for_date)

    # --- Log ---

    def log_action(
        self,
        action_type: str,
        target: Optional[str],
        outcome: str,
        note: Optional[str] = None,
    ) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO action_log(action_type, target, outcome, note, ts) VALUES (?,?,?,?,?)",
                (action_type, target, outcome, note, time.time()),
            )

    def get_today_log(self) -> list[dict]:
        today_start = datetime.combine(date.today(), datetime.min.time()).timestamp()
        rows = self.conn.execute(
            "SELECT * FROM action_log WHERE ts >= ? ORDER BY ts",
            (today_start,),
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Cooldown ---

    def set_cooldown(self, until_ts: float, reason: str) -> None:
        with self.tx() as c:
            c.execute(
                """INSERT OR REPLACE INTO cooldown_state(name, until_ts, reason, triggered_at)
                   VALUES ('global', ?, ?, ?)""",
                (until_ts, reason, time.time()),
            )

    def get_cooldown(self) -> Optional[tuple[float, str]]:
        row = self.conn.execute(
            "SELECT until_ts, reason FROM cooldown_state WHERE name='global'"
        ).fetchone()
        if row and row["until_ts"] > time.time():
            return row["until_ts"], row["reason"]
        return None

    def clear_cooldown(self) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM cooldown_state WHERE name='global'")

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None


# ---------------------------------------------------------------------------
# SafetyMonitor — public API
# ---------------------------------------------------------------------------

class SafetyMonitor:
    """
    Thread-and-process-safe safety monitor.

    All counts are persisted to SQLite so they survive restarts.
    The cooldown state is also persisted — a triggered 2-hour cooldown
    survives a process restart and remains effective.
    """

    def __init__(self, db_path: Path = DB_PATH):
        self._db = _SafetyDB(db_path)

    # --- Core API ---

    async def record(
        self,
        action_type: str,
        target: Optional[str] = None,
        *,
        note: Optional[str] = None,
    ) -> bool:
        """
        Record an intended action and check whether it is allowed.

        Returns True if the action is allowed (and has been counted).
        Returns False if the daily cap would be exceeded or a cooldown is active.
        The caller must NOT perform the action when False is returned.
        """
        # 1. Check global cooldown first
        cooldown = self._db.get_cooldown()
        if cooldown:
            until_ts, reason = cooldown
            remaining = until_ts - time.time()
            logger.warning(
                f"[SAFETY] Cooldown active — {remaining/60:.0f} min remaining "
                f"(reason: {reason}). Skipping {action_type}."
            )
            self._db.log_action(action_type, target, "blocked_cooldown", reason)
            return False

        # 2. Check daily cap
        cap = DAILY_CAPS.get(action_type)
        today = date.today().isoformat()

        if cap is not None:
            current = self._db.get_count(action_type, today)

            if current >= cap:
                msg = (
                    f"[SAFETY] Daily cap reached for '{action_type}': "
                    f"{current}/{cap}. Action blocked."
                )
                logger.warning(msg)
                print(msg, file=sys.stderr)
                self._db.log_action(action_type, target, "blocked_cap", f"{current}/{cap}")
                return False

            # Warn at threshold
            if cap > 0 and (current + 1) / cap >= WARNING_THRESHOLD:
                warn_msg = (
                    f"[SAFETY] ⚠️  Approaching daily cap for '{action_type}': "
                    f"{current + 1}/{cap} ({(current+1)/cap*100:.0f}%)"
                )
                logger.warning(warn_msg)
                print(warn_msg, file=sys.stderr)

        # 3. Increment and log
        new_count = self._db.increment(action_type, today)
        self._db.log_action(action_type, target, "allowed", note)

        logger.info(
            f"[SAFETY] {action_type} recorded — target={target} "
            f"({new_count}/{cap if cap else '∞'} today)"
        )
        return True

    async def record_outcome(
        self,
        action_type: str,
        target: Optional[str],
        success: bool,
        note: Optional[str] = None,
    ) -> None:
        """Log the outcome of an action after it has been performed."""
        outcome = "success" if success else "failed"
        self._db.log_action(action_type, target, outcome, note)

    async def trigger_cooldown(self, reason: str, seconds: int = COOLDOWN_SECONDS) -> None:
        """
        Pause ALL actions for `seconds` seconds.

        Called on any 429 or 403 response.  Persisted to SQLite so it
        survives restarts.
        """
        until_ts = time.time() + seconds
        self._db.set_cooldown(until_ts, reason)
        msg = (
            f"[SAFETY] 🚨 COOLDOWN TRIGGERED — {seconds/3600:.1f}h pause. "
            f"Reason: {reason}. Resumes at "
            f"{datetime.fromtimestamp(until_ts).strftime('%H:%M:%S')}."
        )
        logger.error(msg)
        print(msg, file=sys.stderr)

    def is_in_cooldown(self) -> bool:
        return self._db.get_cooldown() is not None

    def clear_cooldown(self) -> None:
        self._db.clear_cooldown()
        logger.info("[SAFETY] Cooldown cleared manually.")

    # --- Status report ---

    def status(self) -> dict:
        today = date.today().isoformat()
        counts = {}
        for action_type, cap in DAILY_CAPS.items():
            used = self._db.get_count(action_type, today)
            counts[action_type] = {
                "used": used,
                "cap": cap,
                "remaining": max(0, cap - used),
                "pct": round(used / cap * 100, 1) if cap else 0,
            }

        cooldown = self._db.get_cooldown()
        return {
            "date": today,
            "counts": counts,
            "cooldown": {
                "active": cooldown is not None,
                "remaining_seconds": max(0, cooldown[0] - time.time()) if cooldown else 0,
                "reason": cooldown[1] if cooldown else None,
            },
        }

    def print_status(self) -> None:
        s = self.status()
        print(f"\n{'='*50}")
        print(f"  XActions Safety Monitor — {s['date']}")
        print(f"{'='*50}")

        for action, info in s["counts"].items():
            bar_filled = int(info["pct"] / 5)
            bar = "█" * bar_filled + "░" * (20 - bar_filled)
            warn = " ⚠️" if info["pct"] >= WARNING_THRESHOLD * 100 else ""
            print(
                f"  {action:<10} [{bar}] {info['used']:>3}/{info['cap']:<3} "
                f"({info['pct']:>5.1f}%){warn}"
            )

        cd = s["cooldown"]
        if cd["active"]:
            mins = cd["remaining_seconds"] / 60
            print(f"\n  🚨 COOLDOWN ACTIVE — {mins:.0f} min remaining")
            print(f"     Reason: {cd['reason']}")
        else:
            print("\n  ✅ No active cooldown")

        print(f"{'='*50}\n")

    def get_today_log(self) -> list[dict]:
        return self._db.get_today_log()

    def close(self) -> None:
        self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _cli():
    parser = argparse.ArgumentParser(prog="python -m xeepy.safety_monitor")
    parser.add_argument("--status", action="store_true", help="Show today's usage summary")
    parser.add_argument("--log", action="store_true", help="Print today's full action log")
    parser.add_argument("--clear-cooldown", action="store_true", help="Clear active cooldown")
    args = parser.parse_args()

    monitor = SafetyMonitor()

    if args.status:
        monitor.print_status()
    elif args.log:
        log = monitor.get_today_log()
        if not log:
            print("No actions logged today.")
        for entry in log:
            ts = datetime.fromtimestamp(entry["ts"]).strftime("%H:%M:%S")
            print(
                f"{ts}  {entry['action_type']:<10} {entry['outcome']:<20} "
                f"target={entry['target'] or '-'}"
            )
    elif args.clear_cooldown:
        monitor.clear_cooldown()
        print("Cooldown cleared.")
    else:
        parser.print_help()

    monitor.close()


if __name__ == "__main__":
    _cli()
