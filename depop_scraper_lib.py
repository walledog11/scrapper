# depop_scraper_lib.py
import asyncio, random, time, urllib.parse
from typing import List, Dict

# --------- Public API ---------
def scrape_depop(term: str, deep: bool, limits: dict) -> List[Dict]:
    """
    Sync wrapper. Tries real scrape with Playwright; falls back to sample rows if unavailable.
    """
    try:
        from playwright.async_api import async_playwright  # noqa
    except Exception:
        # Fallback so the app stays usable (helps verify Sheets wiring in cloud)
        return [
            {
                "platform": "Depop",
                "brand": "Supreme",
                "item_name": f"{term} (sample)",
                "price": "$199",
                "size": "L",
                "condition": "Good condition",
                "link": f"https://www.depop.com/search/?q={urllib.parse.quote_plus(term)}",
            }
        ]
    # If we have Playwright, run the async crawler
    return asyncio.run(_scrape_depop_async(term, deep, limits))


# --------- Real scraper (lightweight) ---------
async def _scrape_depop_async(term: str, deep: bool, limits: dict) -> List[Dict]:
    from playwright.async_api import async_playwright

    base_url = f"https://www.depop.com/search/?q={urllib.parse.quote_plus(term)}"
    max_items = int(limits.get("MAX_ITEMS", 500))
    max_seconds = int(limits.get("MAX_DURATION_S", 600))

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
        )
        page = await ctx.new_page()
        await page.goto(base_url, wait_until="domcontentloaded", timeout=60000)

        # Best-effort: accept cookies if shown
        try:
            for sel in [
                "button:has-text('Accept')",
                "button:has-text('Accept all')",
                "[data-testid='cookie-accept']",
                "text=Accept cookies",
            ]:
                btn = await page.query_selector(sel)
                if btn:
                    await btn.click()
                    break
        except Exception:
            pass

        # Infinite scroll collector (simple, robust)
        start = time.time()
        seen = set()
        rows: List[Dict] = []

        while True:
            anchors = await page.query_selector_all("a[href^='/products/']")
            for a in anchors:
                href = await a.get_attribute("href")
                if not href or href in seen:
                    continue
                seen.add(href)

                # Pull nearby text for brand/price (very loose heuristic)
                li = await a.evaluate_handle("el => el.closest('li') || el.parentElement")
                price = ""
                brand = ""
                if li:
                    ps = await page.evaluate("""el => Array.from(el.querySelectorAll('p')).map(n => n.textContent || '')""", li)
                    # find something that looks like a currency
                    for t in ps:
                        if any(sym in t for sym in ["$", "£", "€"]):
                            price = t.strip()
                    # last short non-price text as brand
                    for t in reversed(ps):
                        s = (t or "").strip()
                        if s and s != price and len(s) <= 40:
                            brand = s
                            break

                slug = href.rstrip("/").split("/")[-1].replace("-", " ")
                item_name = slug
                if brand and slug.lower().startswith(brand.lower()):
                    item_name = slug[len(brand):].strip()

                rows.append({
                    "platform": "Depop",
                    "brand": brand,
                    "item_name": item_name,
                    "price": price or "N/A",
                    "size": "",
                    "condition": "",
                    "link": f"https://www.depop.com{href}",
                })

                if len(rows) >= max_items:
                    break

            if len(rows) >= max_items:
                break
            if time.time() - start > max_seconds:
                break

            # Scroll and let new cards load
            await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            await page.wait_for_timeout(random.randint(500, 1000))

        await browser.close()
        return rows
