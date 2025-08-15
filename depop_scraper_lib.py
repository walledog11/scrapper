import os, io, csv, time, json, asyncio, random
from typing import List, Dict
from playwright.async_api import async_playwright

def clean_text(text: str) -> str:
    return text.strip().replace("\n", " ").replace("  ", " ")

async def get_item_details(ctx, href):
    size = condition = ""
    try:
        detail_page = await ctx.new_page()
        await detail_page.goto(f"https://www.depop.com{href}", timeout=45000)

        size_elem = await detail_page.query_selector("text='Size' ~ div, text='Size' + div")
        condition_elem = await detail_page.query_selector("text='Condition' ~ div, text='Condition' + div")

        size = await size_elem.inner_text() if size_elem else ""
        condition = await condition_elem.inner_text() if condition_elem else ""
    except:
        pass
    finally:
        await detail_page.close()

    return size, condition

async def _scrape_depop_async(term: str, deep: bool, limits: Dict) -> List[Dict]:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()

        await page.goto(f"https://www.depop.com/search/?q={term.replace(' ', '%20')}", timeout=60000)
        await page.wait_for_selector("a[data-testid='product-card']")

        rows = []
        seen = set()
        idle_rounds = 0
        for _ in range(limits.get("MAX_ROUNDS", 800)):
            anchors = await page.query_selector_all("a[data-testid='product-card']")
            new_items = 0

            for a in anchors:
                try:
                    href = await a.get_attribute("href")
                    if not href or href in seen:
                        continue

                    seen.add(href)
                    title_elem = await a.query_selector("p")
                    item_name = await title_elem.inner_text() if title_elem else "Untitled"

                    price_elem = await a.query_selector("span")
                    price = await price_elem.inner_text() if price_elem else ""

                    brand = ""
                    size = condition = ""

                    if deep and len(rows) < limits.get("DEEP_FETCH_MAX", 1000):
                        size, condition = await get_item_details(ctx, href)

                    rows.append({
                        "platform": "Depop",
                        "brand": brand,
                        "item_name": clean_text(item_name),
                        "price": price.strip(),
                        "size": size,
                        "condition": condition,
                        "link": f"https://www.depop.com{href}"
                    })
                    new_items += 1
                except:
                    continue

            if new_items == 0:
                idle_rounds += 1
                if idle_rounds >= limits.get("IDLE_ROUNDS", 10):
                    break

            await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
            await page.wait_for_timeout(random.randint(
                limits.get("PAUSE_MIN", 500),
                limits.get("PAUSE_MAX", 900)
            ))

            if len(rows) >= limits.get("MAX_ITEMS", 3000):
                break

        await browser.close()
        return rows

def scrape_depop(term: str, deep: bool = False, limits: Dict = {}) -> List[Dict]:
    return asyncio.run(_scrape_depop_async(term, deep, limits))
