"""
Session harvester: opens a visible browser so you can log in manually,
then saves the session to session.json for the daemon.

Requirements (local machine):
  pip install playwright
  playwright install chromium

Usage:
  python3 harvest_session.py
  # then scp session.json to the server:
  # scp session.json user@server:/var/www/XActions/python/xeepy/data/session.json
"""

import asyncio
import sys
from pathlib import Path

SESSION_FILE = Path("session.json")


async def main():
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("Error: playwright not installed.")
        print("Run:  pip install playwright && playwright install chromium")
        sys.exit(1)

    print("Opening browser — log into X/Twitter in the window that appears.")
    print("After you are fully logged in and see your home feed, press ENTER here.")
    print()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=["--start-maximized"],
        )
        ctx = await browser.new_context(
            viewport=None,
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            ),
        )
        page = await ctx.new_page()
        await page.goto("https://x.com/login")

        input("Press ENTER once you are fully logged in and see your home feed... ")

        home = await page.query_selector('[data-testid="primaryColumn"]')
        if not home:
            print("Warning: home timeline not found — make sure you're fully logged in before continuing.")
        else:
            print("Login confirmed.")

        await ctx.storage_state(path=str(SESSION_FILE))
        print(f"\nSession saved to: {SESSION_FILE.resolve()}")
        print()
        print("Next step — copy it to the server:")
        print(f"  scp {SESSION_FILE} user@yourserver:/var/www/XActions/python/xeepy/data/session.json")
        print()
        print("Then on the server:")
        print("  python3 run_daemon.py run")

        await browser.close()


asyncio.run(main())
