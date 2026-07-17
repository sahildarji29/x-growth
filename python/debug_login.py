"""Debug script: attempt login and screenshot the result."""
import asyncio, sys, os, types
sys.path.insert(0, '.')
pkg = types.ModuleType("xeepy"); pkg.__path__ = ["xeepy"]; sys.modules["xeepy"] = pkg

from dotenv import load_dotenv
load_dotenv("xeepy/.env")

from xeepy.core.browser import BrowserManager
from xeepy.core.config import BrowserConfig

USERNAME = os.environ["XEEPY_USERNAME"]
PASSWORD = os.environ["XEEPY_PASSWORD"]

async def debug():
    bm = BrowserManager(BrowserConfig(headless=True))
    await bm.start()
    page = await bm.new_page()

    await page.goto("https://x.com/i/flow/login", wait_until="domcontentloaded")
    await asyncio.sleep(4)

    ok_btn = await page.query_selector("button:has-text('OK')")
    if ok_btn and await ok_btn.is_visible():
        print("Dismissing modal")
        await ok_btn.click()
        await asyncio.sleep(2)

    un = await page.query_selector("input[name='username_or_email']")
    if un:
        await un.fill(USERNAME)
        print("Username filled")
    else:
        print("ERROR: username input not found")

    await asyncio.sleep(0.8)
    pw = await page.query_selector("input[name='password']")
    if pw:
        await pw.fill(PASSWORD)
        print("Password filled")
    else:
        print("ERROR: password input not found")

    await asyncio.sleep(0.8)
    submit = await page.query_selector("button[type='submit']")
    if submit:
        await submit.click()
        print("Submitted, waiting 8s...")
    else:
        print("ERROR: submit button not found")

    await asyncio.sleep(8)
    print("URL after submit:", page.url)
    print("Title:", await page.title())

    # Dump visible text
    body = await page.inner_text("body")
    print("Page text (first 600 chars):", body[:600])

    await page.screenshot(path="/tmp/after_login.png", full_page=False)
    print("Screenshot saved to /tmp/after_login.png")
    await bm.stop()

asyncio.run(debug())
