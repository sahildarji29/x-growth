"""
Interactive X / Twitter login → saves a Playwright session file.

X aggressively fingerprints the browser on its login flow. Playwright's
*bundled* Chromium fails that check (different brand/Client-Hints than real
Chrome, fresh throwaway profile every run), which is why the login page
answers with "We've temporarily limited your login. Please try again later."
even though the exact same credentials work in your normal browser.

This script therefore drives your **real, locally installed Google Chrome**
(Playwright `channel="chrome"`) using a **persistent profile directory**, so
the browser looks and behaves like the one you log in with by hand. The UA
string is left untouched — spoofing it desynchronises the User-Agent from the
Sec-CH-UA / navigator.userAgentData Client Hints, which is itself a bot signal.

Modes:
    python setup_login.py              # open real Chrome, log in normally
    python setup_login.py --chromium   # force Playwright's bundled Chromium
    python setup_login.py --cookies    # no browser: paste auth_token + ct0
                                       # from a browser where you're logged in

Outputs:
    $XEEPY_SESSION_FILE          (default ./data/session.json)  — the session
    <data dir>/user_agent.txt    — UA of the browser that created the session,
                                   reused by growth_bot.py so the replayed
                                   session stays fingerprint-consistent.

The browser modes need a machine with a graphical display. On a headless
server use --cookies, or run this on your laptop and copy the session file.

by nichxbt
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from getpass import getpass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parent
DATA_DIR = Path(os.environ.get("XEEPY_DATA_DIR", ROOT / "data"))
SESSION_FILE = Path(os.environ.get("XEEPY_SESSION_FILE", DATA_DIR / "session.json"))
# Reused across runs so X sees a returning browser instead of a brand-new one.
PROFILE_DIR = Path(os.environ.get("XEEPY_BROWSER_PROFILE", DATA_DIR / "chrome-profile"))
UA_FILE = DATA_DIR / "user_agent.txt"

# Chrome channel to drive: "chrome", "chrome-beta", "msedge", … Empty = bundled Chromium.
BROWSER_CHANNEL = os.environ.get("XEEPY_BROWSER_CHANNEL", "chrome")

LOGIN_TIMEOUT_S = int(os.environ.get("LOGIN_TIMEOUT_S", "600"))  # 10 min to finish login

# Cookies X actually needs to authenticate an API/graphql session.
REQUIRED_COOKIES = ("auth_token", "ct0")


async def _logged_in(context) -> bool:
    """True once X has set the auth_token cookie (the reliable logged-in signal)."""
    for c in await context.cookies():
        if c.get("name") == "auth_token" and c.get("value"):
            return True
    return False


def _save_ua(ua: str) -> None:
    """Persist the real UA so the bot replays the session with the same fingerprint."""
    if not ua:
        return
    UA_FILE.write_text(ua.strip() + "\n", encoding="utf-8")
    print(f"   User agent recorded in {UA_FILE}")


async def _browser_login(use_chromium: bool) -> int:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("❌ Playwright is not installed. Run:  make deps browser", file=sys.stderr)
        return 1

    channel = None if use_chromium else (BROWSER_CHANNEL or None)
    label = "bundled Chromium" if channel is None else f"your installed {channel}"

    print(f"🔐 Opening {label} for X / Twitter login…")
    print("   Log in normally in the window that appears (username, password, 2FA).")
    print(f"   Waiting up to {LOGIN_TIMEOUT_S // 60} min. The window closes automatically once you're in.\n")

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        # A persistent context keeps cookies/localStorage/history between runs, so
        # repeated login attempts don't each look like a fresh burner profile.
        launch_kwargs = dict(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
            no_viewport=True,  # use the real window size; a fixed 1280x900 is a tell
        )
        if channel:
            launch_kwargs["channel"] = channel

        try:
            context = await pw.chromium.launch_persistent_context(**launch_kwargs)
        except Exception as e:
            print(f"❌ Could not open {label}: {e}", file=sys.stderr)
            if channel:
                print(
                    "   Install Google Chrome, or retry with the bundled browser:\n"
                    "       python setup_login.py --chromium\n"
                    "   (the bundled Chromium is more likely to be rate-limited by X)",
                    file=sys.stderr,
                )
            else:
                print(
                    "   This step needs a machine with a graphical display.\n"
                    "   On a headless server use:  python setup_login.py --cookies",
                    file=sys.stderr,
                )
            return 1

        page = context.pages[0] if context.pages else await context.new_page()

        try:
            await page.goto("https://x.com/login", wait_until="domcontentloaded")
        except Exception:
            # Fall back to home if /login redirects oddly
            try:
                await page.goto("https://x.com/home", wait_until="domcontentloaded")
            except Exception:
                pass

        # Poll until the auth_token cookie appears (or the user closes the window / we time out).
        waited = 0
        interval = 2
        try:
            while waited < LOGIN_TIMEOUT_S:
                if page.is_closed():
                    # The user may have logged in and then closed the tab — check first.
                    if await _logged_in(context):
                        break
                    print("❌ Browser window was closed before login completed.", file=sys.stderr)
                    await context.close()
                    return 1
                if await _logged_in(context):
                    break
                await asyncio.sleep(interval)
                waited += interval
            else:
                print("❌ Timed out waiting for login. Re-run and finish logging in.", file=sys.stderr)
                await context.close()
                return 1
        except KeyboardInterrupt:
            print("\n❌ Cancelled.", file=sys.stderr)
            await context.close()
            return 1

        # Give X a moment to set the remaining cookies (ct0, etc.), then save.
        await asyncio.sleep(3)

        ua = ""
        try:
            if not page.is_closed():
                ua = await page.evaluate("() => navigator.userAgent")
        except Exception:
            pass

        await context.storage_state(path=str(SESSION_FILE))
        await context.close()

    _save_ua(ua)
    return 0


def _cookie_login() -> int:
    """Build a session file from cookies copied out of a browser you're logged into.

    Where to find them: log in to x.com in your normal browser →
    DevTools (F12) → Application ▸ Storage ▸ Cookies ▸ https://x.com →
    copy the values of `auth_token` and `ct0`.

    ⚠️  These two cookies grant full access to your X account. Treat them like a
    password: never paste them into a shared machine, a chat, or a commit.
    """
    print("🍪 Manual cookie import (no browser needed).")
    print("   In a browser where you ARE logged in to x.com:")
    print("     DevTools (F12) → Application → Cookies → https://x.com")
    print("   Copy the values of 'auth_token' and 'ct0'.")
    print("   ⚠️  These grant full access to your account — never share them.\n")

    values: dict[str, str] = {}
    for name in REQUIRED_COOKIES:
        # getpass keeps the secrets off the screen and out of shell history.
        raw = getpass(f"   {name}: ").strip().strip('"').strip("'")
        if not raw:
            print(f"❌ {name} is required.", file=sys.stderr)
            return 1
        values[name] = raw

    # X serves both domains; a cookie set on .x.com is not sent to twitter.com.
    expires = int(time.time()) + 60 * 60 * 24 * 365  # 1 year; X refreshes on use
    cookies = [
        {
            "name": name,
            "value": value,
            "domain": domain,
            "path": "/",
            "expires": expires,
            "httpOnly": name == "auth_token",  # ct0 must stay readable by JS (CSRF header)
            "secure": True,
            "sameSite": "Lax",
        }
        for domain in (".x.com", ".twitter.com")
        for name, value in values.items()
    ]

    SESSION_FILE.write_text(
        json.dumps({"cookies": cookies, "origins": []}, indent=2),
        encoding="utf-8",
    )
    # Restrict to the owner — this file is equivalent to a password.
    os.chmod(SESSION_FILE, 0o600)
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(description="Log in to X and save a Playwright session.")
    parser.add_argument(
        "--chromium",
        action="store_true",
        help="force Playwright's bundled Chromium instead of your installed Chrome",
    )
    parser.add_argument(
        "--cookies",
        action="store_true",
        help="skip the browser: paste auth_token + ct0 from a logged-in browser",
    )
    args = parser.parse_args()

    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)

    rc = _cookie_login() if args.cookies else await _browser_login(args.chromium)
    if rc != 0:
        return rc

    print(f"\n✅ Login saved to {SESSION_FILE}")
    print("   You're all set — start the bot with:  make run")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
