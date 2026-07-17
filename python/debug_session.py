"""Debug: test session.json loading directly."""
import asyncio, sys, os, types, json
sys.path.insert(0, '.')
pkg = types.ModuleType("xeepy"); pkg.__path__ = ["xeepy"]; sys.modules["xeepy"] = pkg

from pathlib import Path
from playwright.async_api import async_playwright

SESSION = Path("xeepy/data/session.json")

async def main():
    print(f"Session file: {SESSION} ({SESSION.stat().st_size} bytes)")

    # Peek at cookie count
    data = json.loads(SESSION.read_text())
    cookies = data.get("cookies", [])
    print(f"Cookies in file: {len(cookies)}")
    # Show key auth cookie names
    key_names = {"auth_token", "ct0", "twid", "kdt"}
    found = [c["name"] for c in cookies if c["name"] in key_names]
    print(f"Key auth cookies present: {found}")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            storage_state=str(SESSION),
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            ),
        )
        page = await ctx.new_page()

        print("Navigating to x.com/home ...")
        await page.goto("https://x.com/home", wait_until="domcontentloaded")
        await asyncio.sleep(5)

        print("URL:", page.url)
        print("Title:", await page.title())

        home = await page.query_selector('[data-testid="primaryColumn"]')
        print("Home timeline found:", home is not None)

        body = await page.inner_text("body")
        print("Body[:400]:", body[:400])

        await page.screenshot(path="/tmp/session_test.png")
        print("Screenshot: /tmp/session_test.png")
        await browser.close()

asyncio.run(main())
