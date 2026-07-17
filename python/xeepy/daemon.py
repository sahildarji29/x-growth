"""
XActions Growth Daemon

Background automation daemon for organic X/Twitter growth.
Orchestrates likes, AI comments, and follows across the day
within all SafetyMonitor hard caps.

Commands:
  python -m xeepy.daemon start    # Fork to background and run
  python -m xeepy.daemon stop     # Stop the running daemon
  python -m xeepy.daemon status   # Show daemon + safety status
  python -m xeepy.daemon run      # Run in foreground (debug / systemd)
  python -m xeepy.daemon restart  # Stop then start

Environment variables (all optional — see .env.example):
  DAEMON_PID_FILE, DAEMON_STATUS_FILE, DAEMON_LOG_FILE,
  DAEMON_ACTIVE_HOURS_START, DAEMON_ACTIVE_HOURS_END,
  DAEMON_CYCLE_MIN_MINUTES, DAEMON_CYCLE_MAX_MINUTES,
  TARGET_KEYWORDS, TARGET_ACCOUNTS
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import signal
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from enum import Enum
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from loguru import logger

load_dotenv()


# ---------------------------------------------------------------------------
# Runtime paths  (all configurable via .env)
# ---------------------------------------------------------------------------

_DATA_DIR = Path(os.environ.get("XEEPY_DATA_DIR", "./data"))
_LOGS_DIR = Path(os.environ.get("XEEPY_LOG_DIR", "./logs"))

PID_FILE    = Path(os.environ.get("DAEMON_PID_FILE",    _DATA_DIR / "xeepy_daemon.pid"))
STATUS_FILE = Path(os.environ.get("DAEMON_STATUS_FILE", _DATA_DIR / "daemon_status.json"))
LOG_FILE    = Path(os.environ.get("DAEMON_LOG_FILE",    _LOGS_DIR / "daemon.log"))
SESSION_FILE = Path(os.environ.get("XEEPY_SESSION_FILE", _DATA_DIR / "session.json"))

# Active hours — daemon only acts within this window (24h clock)
ACTIVE_HOURS_START = int(os.environ.get("DAEMON_ACTIVE_HOURS_START", "9"))
ACTIVE_HOURS_END   = int(os.environ.get("DAEMON_ACTIVE_HOURS_END",   "22"))

# Cycle timing — how long between action rounds (minutes, randomised)
CYCLE_MIN = int(os.environ.get("DAEMON_CYCLE_MIN_MINUTES", "45"))
CYCLE_MAX = int(os.environ.get("DAEMON_CYCLE_MAX_MINUTES", "90"))

# Niche keywords for search-based liking/following/commenting
_DEFAULT_KEYWORDS = [
    "AI automation", "LLM", "coding agents", "agentic AI", "vibe coding",
    "AI models", "developer tools", "open source AI", "future of software",
    "Laravel AI", "agentic workflows",
]
TARGET_KEYWORDS: list[str] = [
    k.strip() for k in
    os.environ.get("TARGET_KEYWORDS", ",".join(_DEFAULT_KEYWORDS)).split(",")
    if k.strip()
]

# Optional: specific accounts whose followers to target
TARGET_ACCOUNTS: list[str] = [
    a.strip().lstrip("@") for a in
    os.environ.get("TARGET_ACCOUNTS", "").split(",")
    if a.strip()
]


# ---------------------------------------------------------------------------
# Action types & scheduling
# ---------------------------------------------------------------------------

class ActionType(Enum):
    LIKE    = "like"
    COMMENT = "comment"
    FOLLOW  = "follow"


@dataclass
class ActionSchedule:
    """Tracks when each action type should next run."""
    next_run: dict[ActionType, float] = field(
        default_factory=lambda: {a: 0.0 for a in ActionType}
    )
    counts_today: dict[str, int] = field(default_factory=dict)
    last_run_date: str = ""

    def reset_if_new_day(self) -> None:
        today = date.today().isoformat()
        if self.last_run_date != today:
            self.counts_today = {}
            self.last_run_date = today
            logger.info("Midnight reset — daily action counts cleared.")

    def increment(self, action: ActionType) -> None:
        self.counts_today[action.value] = self.counts_today.get(action.value, 0) + 1

    def schedule_next(self, action: ActionType, after_minutes: Optional[float] = None) -> None:
        """Set the earliest next run time for an action."""
        delay = (
            after_minutes
            if after_minutes is not None
            else random.uniform(CYCLE_MIN, CYCLE_MAX)
        )
        self.next_run[action] = time.time() + delay * 60

    def due_actions(self) -> list[ActionType]:
        """Return actions that are due right now, ordered by how overdue they are."""
        now = time.time()
        return sorted(
            [a for a in ActionType if self.next_run[a] <= now],
            key=lambda a: self.next_run[a],
        )


# ---------------------------------------------------------------------------
# Status file
# ---------------------------------------------------------------------------

@dataclass
class DaemonStatus:
    pid: int = 0
    state: str = "stopped"           # starting | running | sleeping | stopping
    started_at: str = ""
    last_action: str = ""
    last_action_at: str = ""
    next_action_at: str = ""
    cycles_completed: int = 0
    errors_today: int = 0
    cooldown_active: bool = False
    counts_today: dict = field(default_factory=dict)

    def save(self) -> None:
        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATUS_FILE.write_text(json.dumps(asdict(self), indent=2))

    @staticmethod
    def load() -> "DaemonStatus":
        try:
            return DaemonStatus(**json.loads(STATUS_FILE.read_text()))
        except Exception:
            return DaemonStatus()


# ---------------------------------------------------------------------------
# GrowthDaemon — core orchestrator
# ---------------------------------------------------------------------------

class GrowthDaemon:
    """
    Background daemon that orchestrates likes, AI comments, and follows.

    Action flow per cycle:
      1. Pick the most overdue action (like → comment → follow, round-robin)
      2. Check SafetyMonitor for remaining daily cap
      3. Execute a small batch (2–5 per cycle, randomised)
      4. Sleep CYCLE_MIN–CYCLE_MAX minutes before next cycle

    The daemon only acts within ACTIVE_HOURS_START–ACTIVE_HOURS_END.
    Outside that window it sleeps until the next active period.
    """

    def __init__(self) -> None:
        self._shutdown = False
        self._schedule = ActionSchedule()
        self._status = DaemonStatus(
            pid=os.getpid(),
            state="starting",
            started_at=datetime.now().isoformat(),
        )
        self._browser = None
        self._auth = None
        self._safety = None
        self._rate_limiter = None

    # ------------------------------------------------------------------
    # Startup / shutdown
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Main entry point — runs until shutdown signal."""
        self._setup_logging()
        self._setup_signals()

        logger.info(f"Growth daemon starting (PID {os.getpid()})")
        logger.info(f"Active hours: {ACTIVE_HOURS_START}:00–{ACTIVE_HOURS_END}:00")
        logger.info(f"Cycle interval: {CYCLE_MIN}–{CYCLE_MAX} min")
        logger.info(f"Keywords: {TARGET_KEYWORDS}")

        try:
            await self._init_services()
            await self._login()
            self._status.state = "running"
            self._status.save()
            await self._main_loop()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Fatal daemon error: {e}")
            self._status.state = "error"
            self._status.save()
            raise
        finally:
            await self._shutdown_services()
            PID_FILE.unlink(missing_ok=True)
            self._status.state = "stopped"
            self._status.save()
            logger.info("Daemon stopped cleanly.")

    async def _init_services(self) -> None:
        """Lazy-import heavy deps so `daemon stop` stays instant."""
        from xeepy.core.browser import BrowserManager
        from xeepy.core.auth import AuthManager
        from xeepy.core.config import BrowserConfig
        from xeepy.core.rate_limiter import ActionRateLimiter
        from xeepy.safety_monitor import SafetyMonitor

        headless = os.environ.get("XEEPY_HEADLESS", "true").lower() != "false"
        browser_cfg = BrowserConfig(headless=headless)

        self._browser = BrowserManager(config=browser_cfg)
        await self._browser.start()
        await self._browser.new_context(storage_state=SESSION_FILE if SESSION_FILE.exists() else None)
        await self._browser.new_page()

        self._auth = AuthManager(self._browser)
        self._safety = SafetyMonitor()
        self._rate_limiter = ActionRateLimiter()

        logger.info("Services initialised.")

    async def _login(self) -> None:
        username = os.environ.get("XEEPY_USERNAME")
        password = os.environ.get("XEEPY_PASSWORD")

        if not username or not password:
            raise RuntimeError(
                "XEEPY_USERNAME and XEEPY_PASSWORD must be set in .env"
            )

        success = await self._auth.login(
            username=username,
            password=password,
            session_file=SESSION_FILE,
        )
        if not success:
            raise RuntimeError("Authentication failed — check credentials.")
        logger.info(f"Logged in as @{username}")

    async def _shutdown_services(self) -> None:
        if self._browser:
            try:
                await self._browser.stop()
            except Exception:
                pass
        if self._safety:
            self._safety.close()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _main_loop(self) -> None:
        while not self._shutdown:
            self._schedule.reset_if_new_day()

            # Outside active hours — sleep until window opens
            if not self._in_active_hours():
                wake_seconds = self._seconds_until_active()
                next_wake = datetime.fromtimestamp(time.time() + wake_seconds)
                logger.info(
                    f"Outside active hours — sleeping until "
                    f"{next_wake.strftime('%H:%M:%S')} ({wake_seconds/60:.0f} min)"
                )
                self._status.state = "sleeping"
                self._status.next_action_at = next_wake.isoformat()
                self._status.save()
                await self._interruptible_sleep(wake_seconds)
                continue

            # Global cooldown
            if self._safety.is_in_cooldown():
                logger.warning("Cooldown active — sleeping 10 minutes.")
                self._status.cooldown_active = True
                self._status.save()
                await self._interruptible_sleep(600)
                continue
            self._status.cooldown_active = False

            # Pick due action(s) — process one per cycle
            due = self._schedule.due_actions()
            if not due:
                # Nothing due yet — sleep until the soonest scheduled action
                soonest = min(self._schedule.next_run.values())
                wait = max(60, soonest - time.time())
                logger.debug(f"No actions due — sleeping {wait/60:.1f} min")
                await self._interruptible_sleep(wait)
                continue

            action = due[0]
            logger.info(f"Cycle starting — action: {action.value}")
            self._status.state = "running"
            self._status.save()

            try:
                await self._execute_action(action)
                self._schedule.increment(action)
                self._status.cycles_completed += 1
            except Exception as e:
                logger.error(f"Action {action.value} failed: {e}")
                self._status.errors_today += 1
                # Back off on repeated errors
                await self._interruptible_sleep(300)

            # Schedule next run for this action type
            # Spread different action types so they don't all cluster
            spread = {
                ActionType.LIKE:    random.uniform(CYCLE_MIN, CYCLE_MAX),
                ActionType.COMMENT: random.uniform(CYCLE_MIN * 2, CYCLE_MAX * 2),
                ActionType.FOLLOW:  random.uniform(CYCLE_MIN * 1.5, CYCLE_MAX * 1.5),
            }
            self._schedule.schedule_next(action, after_minutes=spread[action])

            next_ts = self._schedule.next_run[action]
            self._status.counts_today = self._schedule.counts_today.copy()
            self._status.last_action = action.value
            self._status.last_action_at = datetime.now().isoformat()
            self._status.next_action_at = datetime.fromtimestamp(next_ts).isoformat()
            self._status.state = "sleeping"
            self._status.save()

            # Short inter-cycle pause before checking for next due action
            await self._interruptible_sleep(random.uniform(30, 90))

    # ------------------------------------------------------------------
    # Action executors
    # ------------------------------------------------------------------

    async def _execute_action(self, action: ActionType) -> None:
        if action == ActionType.LIKE:
            await self._run_likes()
        elif action == ActionType.COMMENT:
            await self._run_comments()
        elif action == ActionType.FOLLOW:
            await self._run_follows()

    async def _run_likes(self) -> None:
        """Like 3–5 niche tweets this cycle."""
        from xeepy.actions.engagement.like.auto_liker import AutoLiker
        from xeepy.actions.types import AutoLikeConfig

        batch = random.randint(3, 5)
        logger.info(f"Like cycle — targeting {batch} tweets")

        config = AutoLikeConfig(
            keywords=random.sample(TARGET_KEYWORDS, min(4, len(TARGET_KEYWORDS))),
            max_likes_per_session=batch,
            max_likes_per_hour=batch,        # effectively one batch per call
            delay_range=(8, 35),
            like_probability=0.85,
            exclude_retweets=True,
            min_likes=5,
            max_likes=5000,
        )

        liker = AutoLiker(
            browser=self._browser,
            rate_limiter=self._rate_limiter.get("like"),
            safety_monitor=self._safety,
        )

        result = await liker.execute(config=config, duration_minutes=15)
        logger.info(
            f"Like cycle done — {result.success_count} liked, "
            f"{result.skipped_count} skipped, {result.failed_count} failed"
        )

    async def _run_comments(self) -> None:
        """Comment on 1–2 niche tweets this cycle via Claude."""
        from xeepy.actions.engagement.comment.ai_commenter import AICommenter
        from xeepy.actions.types import AutoCommentConfig

        batch = random.randint(1, 2)
        logger.info(f"Comment cycle — targeting {batch} tweets")

        config = AutoCommentConfig(
            keywords=random.sample(TARGET_KEYWORDS, min(3, len(TARGET_KEYWORDS))),
            max_comments_per_session=batch,
            max_comments_per_hour=batch,
            delay_range=(30, 75),
        )

        commenter = AICommenter(
            browser=self._browser,
            rate_limiter=self._rate_limiter.get("comment"),
            safety_monitor=self._safety,
        )

        result = await commenter.execute(config=config, duration_minutes=20)
        logger.info(
            f"Comment cycle done — {result.success_count} posted, "
            f"{result.skipped_count} skipped"
        )

    async def _run_follows(self) -> None:
        """Follow 2–4 real accounts in niche this cycle."""
        from xeepy.actions.follow.follow_by_keyword import FollowByKeyword
        from xeepy.actions.base import RateLimiter, RateLimitConfig
        from xeepy.actions.base import FollowFilters

        batch = random.randint(2, 4)
        keyword = random.choice(TARGET_KEYWORDS)
        logger.info(f"Follow cycle — up to {batch} follows via '{keyword}'")

        # Check daily cap before we even open the browser for this
        allowed = await self._safety.record("follow", target=f"batch:{keyword}")
        if not allowed:
            logger.info("Daily follow cap reached — skipping follow cycle.")
            return

        filters = FollowFilters(
            min_followers=200,
            max_followers=50000,
            min_tweets=20,
            must_have_bio=True,
            must_have_profile_pic=True,
            exclude_protected=True,
        )

        rl_config = RateLimitConfig(min_delay=60.0, max_delay=120.0)
        rate_limiter = RateLimiter(config=rl_config)

        action = FollowByKeyword(
            browser=self._browser,
            rate_limiter=rate_limiter,
        )

        result = await action.execute(
            keywords=[keyword],
            max_follows=batch,
            filters=filters,
            skip_previously_followed=True,
        )

        for username in result.followed_users:
            await self._safety.record_outcome("follow", username, True)
        for username in result.failed_users:
            await self._safety.record_outcome("follow", username, False)

        logger.info(
            f"Follow cycle done — {result.success_count} followed, "
            f"{result.skipped_count} skipped"
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _in_active_hours(self) -> bool:
        h = datetime.now().hour
        return ACTIVE_HOURS_START <= h < ACTIVE_HOURS_END

    def _seconds_until_active(self) -> float:
        now = datetime.now()
        next_start = now.replace(
            hour=ACTIVE_HOURS_START, minute=random.randint(0, 15),
            second=0, microsecond=0
        )
        if next_start <= now:
            from datetime import timedelta
            next_start += timedelta(days=1)
        return (next_start - now).total_seconds()

    async def _interruptible_sleep(self, seconds: float) -> None:
        """Sleep in short chunks so SIGTERM is handled promptly."""
        end = time.time() + seconds
        while not self._shutdown and time.time() < end:
            await asyncio.sleep(min(5, end - time.time()))

    def _setup_signals(self) -> None:
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._handle_shutdown)

    def _handle_shutdown(self) -> None:
        logger.info("Shutdown signal received — stopping after current action.")
        self._shutdown = True
        self._status.state = "stopping"
        self._status.save()

    @staticmethod
    def _setup_logging() -> None:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        logger.remove()
        logger.add(sys.stderr, level=os.environ.get("XEEPY_LOG_LEVEL", "INFO"))
        logger.add(
            str(LOG_FILE),
            level="DEBUG",
            rotation="10 MB",
            retention="14 days",
            compression="gz",
        )


# ---------------------------------------------------------------------------
# Process management (start / stop / status)
# ---------------------------------------------------------------------------

def _write_pid() -> None:
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))


def _read_pid() -> Optional[int]:
    try:
        return int(PID_FILE.read_text().strip())
    except Exception:
        return None


def _is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def cmd_run() -> None:
    """Run daemon in foreground (used by systemd or direct invocation)."""
    from xeepy.safety_monitor import SingleInstanceGuard
    with SingleInstanceGuard():
        _write_pid()
        asyncio.run(GrowthDaemon().run())


def cmd_start() -> None:
    """Fork daemon to background."""
    pid = _read_pid()
    if pid and _is_running(pid):
        print(f"Daemon is already running (PID {pid}).")
        sys.exit(1)

    # Double-fork to detach from terminal
    child = os.fork()
    if child > 0:
        print(f"Daemon started (PID {child}). Logs: {LOG_FILE}")
        sys.exit(0)

    os.setsid()
    grandchild = os.fork()
    if grandchild > 0:
        sys.exit(0)

    # Redirect stdio
    sys.stdout.flush()
    sys.stderr.flush()
    with open(os.devnull, "r") as devnull:
        os.dup2(devnull.fileno(), sys.stdin.fileno())
    with open(str(LOG_FILE), "a") as log:
        os.dup2(log.fileno(), sys.stdout.fileno())
        os.dup2(log.fileno(), sys.stderr.fileno())

    cmd_run()


def cmd_stop() -> None:
    """Send SIGTERM to the running daemon."""
    pid = _read_pid()
    if not pid or not _is_running(pid):
        print("Daemon is not running.")
        sys.exit(0)

    os.kill(pid, signal.SIGTERM)

    # Wait up to 10 seconds for clean exit
    for _ in range(20):
        time.sleep(0.5)
        if not _is_running(pid):
            print(f"Daemon (PID {pid}) stopped.")
            PID_FILE.unlink(missing_ok=True)
            return
    print(f"Daemon (PID {pid}) did not stop — send SIGKILL manually.")
    sys.exit(1)


def cmd_restart() -> None:
    cmd_stop()
    time.sleep(1)
    cmd_start()


def cmd_status() -> None:
    """Print daemon + SafetyMonitor status."""
    from xeepy.safety_monitor import SafetyMonitor

    pid = _read_pid()
    running = pid is not None and _is_running(pid)

    status = DaemonStatus.load()

    print(f"\n{'='*52}")
    print(f"  XActions Growth Daemon")
    print(f"{'='*52}")
    print(f"  State   : {'🟢 RUNNING' if running else '🔴 STOPPED'}", end="")
    print(f" (PID {pid})" if running else "")
    if status.started_at:
        print(f"  Started : {status.started_at[:19]}")
    if status.last_action:
        print(f"  Last    : {status.last_action} @ {status.last_action_at[:19]}")
    if status.next_action_at:
        print(f"  Next    : {status.next_action_at[:19]}")
    print(f"  Cycles  : {status.cycles_completed}")
    if status.errors_today:
        print(f"  Errors  : ⚠️  {status.errors_today} today")

    monitor = SafetyMonitor()
    monitor.print_status()
    monitor.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m xeepy.daemon",
        description="XActions Growth Daemon",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("start",   help="Start daemon in background")
    sub.add_parser("stop",    help="Stop running daemon")
    sub.add_parser("restart", help="Restart daemon")
    sub.add_parser("run",     help="Run in foreground (debug / systemd)")
    sub.add_parser("status",  help="Show daemon and safety status")

    args = parser.parse_args()

    dispatch = {
        "start":   cmd_start,
        "stop":    cmd_stop,
        "restart": cmd_restart,
        "run":     cmd_run,
        "status":  cmd_status,
    }
    dispatch[args.command]()


if __name__ == "__main__":
    main()
