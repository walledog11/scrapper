import asyncio
from playwright.async_api import async_playwright
import json

async def save_cookies():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto("https://www.depop.com")

        print("Please accept cookies manually in the opened browser.")
        input("Press Enter here after accepting cookies...")

        cookies = await context.cookies()
        with open("depop_cookies.json", "w") as f:
            json.dump(cookies, f)
        print("✅ Cookies saved to depop_cookies.json")

        await browser.close()

asyncio.run(save_cookies())
