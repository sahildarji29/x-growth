"""
Interactive X / Twitter login → saves a Playwright session file.

Opens a real (visible) Chromium window, lets you log in to X normally
(username/password, 2FA, passkey — whatever your account uses), then
automatically detects the login and writes the session to your
XEEPY_SESSION_FILE (default: ./data/session.json). The Growth Bot reuses
that session so it never has to log in again.

Usage:
    python setup_login.py

Requires a machine with a graphical display (it opens a browser window).
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parent
DATA_DIR = Path(os.environ.get("XEEPY_DATA_DIR", ROOT / "data"))
SESSION_FILE = Path(os.environ.get("XEEPY_SESSION_FILE", DATA_DIR / "session.json"))

# Match the bot's user agent so the saved session is consistent.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

LOGIN_TIMEOUT_S = int(os.environ.get("LOGIN_TIMEOUT_S", "600"))  # 10 min to finish login


async def _logged_in(context) -> bool:
    """True once X has set the auth_token cookie (the reliable logged-in signal)."""
    for c in await context.cookies():
        if c.get("name") == "auth_token" and c.get("value"):
            return True
    return False


async def main() -> int:
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("❌ Playwright is not installed. Run:  make deps browser", file=sys.stderr)
        return 1

    print("🔐 Opening Chromium for X / Twitter login…")
    print("   Log in normally in the window that appears (username, password, 2FA).")
    print(f"   Waiting up to {LOGIN_TIMEOUT_S // 60} min. The window closes automatically once you're in.\n")

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
            )
        except Exception as e:
            print(f"❌ Could not open a browser window: {e}", file=sys.stderr)
            print(
                "   This step needs a machine with a graphical display.\n"
                "   If you're on a headless server, run login on your laptop and copy\n"
                f"   the resulting session file to {SESSION_FILE}.",
                file=sys.stderr,
            )
            return 1

        context = await browser.new_context(user_agent=USER_AGENT, viewport={"width": 1280, "height": 900})
        page = await context.new_page()

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
                    print("❌ Browser window was closed before login completed.", file=sys.stderr)
                    await browser.close()
                    return 1
                if await _logged_in(context):
                    break
                await asyncio.sleep(interval)
                waited += interval
            else:
                print("❌ Timed out waiting for login. Re-run and finish logging in.", file=sys.stderr)
                await browser.close()
                return 1
        except KeyboardInterrupt:
            print("\n❌ Cancelled.", file=sys.stderr)
            await browser.close()
            return 1

        # Give X a moment to set the remaining cookies (ct0, etc.), then save.
        await asyncio.sleep(3)
        await context.storage_state(path=str(SESSION_FILE))
        await browser.close()

    print(f"\n✅ Login saved to {SESSION_FILE}")
    print("   You're all set — start the bot with:  make run")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
