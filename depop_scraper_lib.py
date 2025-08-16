# depop_scraper_lib.py
# Memory-lean Depop scraper with Playwright.
# Public API remains: scrape_depop(term: str, deep: bool, limits: dict) -> List[Dict]

from __future__ import annotations
import os, time, asyncio
from typing import List, Dict, Optional
from urllib.parse import quote_plus

def scrape_depop(term: str, deep: bool, limits: dict) -> List[Dict]:
    """Sync wrapper. Returns a sample row on failure so UI doesn't crash."""
    try:
        from playwright.async_api import async_playwright  # noqa: F401
    except Exception:
        return [_sample_row(term)]

    # Ensure we only run one event loop at a time
    try:
        return asyncio.run(_scrape_depop_async(term, deep, limits or {}))
    except RuntimeError:
        # Already inside a loop (some local IDEs) — run with a new loop
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_scrape_depop_async(term, deep, limits or {}))
        finally:
            loop.close()
    except Exception:
        return [_sample_row(term)]


# ---------------- Async impl ----------------

from playwright.async_api import async_playwright, TimeoutError as PWTimeout

CARD_SELECTORS = [
    "a[href^='/products/']",
    "[data-testid='product-card'] a",
    "a[data-testid^='product-']",
]

TITLE_SELECTORS = [
    "[data-testid='product-title']",
    "h1",
    "div[dir='auto']",
]

PRICE_SELECTORS = [
    "span[data-testid='price-label']",
    "[data-testid='product-price']",
    "span:has-text('$'), span:has-text('£'), span:has-text('€')",
]

COOKIE_SELECTORS = [
    "button:has-text('Accept all')",
    "button:has-text('Accept')",
    "[data-testid='cookie-accept']",
    "text=Accept cookies",
]

# Domains we can skip entirely to save RAM/BW
BLOCK_HOST_SUBSTR = [
    "google-analytics", "googletag", "doubleclick", "facebook", "tiktok",
    "analytics", "segment", "optimizely", "hotjar", "sentry", "cdn-cookielaw"
]

# Resource types to block (keep CSS so the DOM still lays out consistently)
BLOCK_TYPES = {"image", "media", "font"}


async def _scrape_depop_async(query: str, deep: bool, limits: dict) -> List[Dict]:
    MAX_ITEMS        = int(limits.get("MAX_ITEMS", 200))
    MAX_DURATION_S   = int(limits.get("MAX_DURATION_S", 60))
    SCROLL_ROUNDS    = int(limits.get("MAX_ROUNDS", 30))
    PAUSE_MS         = int(limits.get("PAUSE_MIN", 350))
    DETAIL_TIMEOUT   = int(limits.get("DETAIL_TIMEOUT_MS", 20000))
    NETWORK_IDLE_MS  = int(limits.get("NETWORK_IDLE_MS", 8000))

    search_url = (
        "https://www.depop.com/search/"
        f"?q={quote_plus(query)}&sort=relevance&country=us&currency=usd"
    )

    launch_args = [
        "--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage",
        "--disable-gpu", "--disable-background-networking",
        "--disable-background-timer-throttling",
    ]

    listings: List[Dict] = []
    t0 = time.time()

    async with async_playwright() as p:
        # Launch browser (Chromium preferred)
        browser = None
        for bt in (p.chromium, p.firefox):
            try:
                browser = await bt.launch(headless=True, args=launch_args)
                break
            except Exception:
                continue
        if not browser:
            return [_sample_row(query)]

        # One context, request blocking on to reduce RAM/network
        context = await browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )

        async def _route_handler(route):
            req = route.request
            if req.resource_type in BLOCK_TYPES:
                return await route.abort()
            url = req.url.lower()
            if any(bad in url for bad in BLOCK_HOST_SUBSTR):
                return await route.abort()
            return await route.continue_()

        await context.route("**/*", _route_handler)

        page = await context.new_page()

        # Go to search
        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            await context.close(); await browser.close()
            return [_sample_row(query)]

        # Accept cookies if present
        await _maybe_click(page, COOKIE_SELECTORS)

        try:
            await page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_MS)
        except PWTimeout:
            pass

        # Progressive scroll & collect links (no element snapshots)
        links = await _collect_links(page, SCROLL_ROUNDS, CARD_SELECTORS, PAUSE_MS, NETWORK_IDLE_MS)
        links = list(dict.fromkeys(links))[:MAX_ITEMS]  # dedupe, cap

        # Reuse a single detail page to keep memory low
        detail = await context.new_page()
        await context.route("**/*", _route_handler)  # also block in detail page

        for link in links:
            if time.time() - t0 > MAX_DURATION_S:
                break
            item = await _read_detail(detail, link, deep=deep, timeout_ms=DETAIL_TIMEOUT)
            if not item["item_name"]:
                slug = link.rstrip("/").split("/")[-1].replace("-", " ")
                item["item_name"] = slug
            listings.append(item)

        await detail.close()
        await context.close()
        await browser.close()

    return listings


# ---------------- helpers ----------------

async def _maybe_click(page, selectors: List[str]) -> None:
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0:
                await loc.first.click(timeout=800)
                await page.wait_for_timeout(250)
                return
        except Exception:
            pass

async def _collect_links(page, rounds: int, selectors: List[str], pause_ms: int, idle_ms: int) -> List[str]:
    links: List[str] = []
    seen = set()

    # Ensure at least something is attached
    attached = False
    for sel in selectors:
        try:
            await page.wait_for_selector(sel, state="attached", timeout=8000)
            attached = True
            break
        except PWTimeout:
            continue
    if not attached:
        return links

    for _ in range(rounds):
        for sel in selectors:
            try:
                cards = page.locator(sel)
                n = await cards.count()
                for i in range(n):
                    a = cards.nth(i)
                    href = await a.get_attribute("href")
                    if not href:
                        continue
                    link = f"https://www.depop.com{href}" if href.startswith("/") else href
                    if link in seen:
                        continue
                    seen.add(link)
                    links.append(link)
            except Exception:
                pass

        # Scroll and let requests settle
        try:
            await page.evaluate("window.scrollBy(0, Math.round(window.innerHeight * 0.9));")
        except Exception:
            pass
        try:
            await page.wait_for_load_state("networkidle", timeout=idle_ms)
        except PWTimeout:
            pass
        await page.wait_for_timeout(pause_ms)

        # If no growth for a while, break
        if len(links) >= 20 and _ > 6 and len(links) == len(seen):
            break

    return links

async def _read_detail(page, link: str, deep: bool, timeout_ms: int) -> Dict:
    out = {
        "platform": "Depop",
        "brand": "",
        "item_name": "",
        "price": "",
        "size": "",
        "condition": "",
        "link": link,
    }
    try:
        resp = await page.goto(link, wait_until="domcontentloaded", timeout=timeout_ms)
        if not resp or not resp.ok:
            return out

        # Title
        for sel in TITLE_SELECTORS:
            try:
                el = page.locator(sel).first
                await el.wait_for(state="attached", timeout=2000)
                txt = (await el.inner_text()).strip()
                if txt:
                    out["item_name"] = txt
                    break
            except Exception:
                pass

        # Price
        for sel in PRICE_SELECTORS:
            try:
                el = page.locator(sel).first
                await el.wait_for(state="attached", timeout=2000)
                txt = (await el.inner_text()).strip()
                if any(c in txt for c in ("$", "£", "€")):
                    out["price"] = txt
                    break
            except Exception:
                pass

        if deep:
            # Light heuristic text scan for size/condition/brand
            try:
                body = await page.inner_text("body")
                for line in body.splitlines():
                    s = line.strip()
                    if not s:
                        continue
                    low = s.lower()
                    if "size" in low and not out["size"]:
                        parts = s.split(":", 1)
                        out["size"] = parts[1].strip() if len(parts) > 1 else s
                    if "condition" in low and not out["condition"]:
                        parts = s.split(":", 1)
                        out["condition"] = parts[1].strip() if len(parts) > 1 else s
                    if "brand" in low and not out["brand"]:
                        parts = s.split(":", 1)
                        out["brand"] = parts[1].strip() if len(parts) > 1 else s
            except Exception:
                pass

    except Exception:
        pass
    return out


def _sample_row(term: str) -> Dict:
    return {
        "platform": "Depop",
        "brand": "Sample",
        "item_name": f"{term} (sample)",
        "price": "$99",
        "size": "M",
        "condition": "Good",
        "link": f"https://www.depop.com/search/?q={quote_plus(term)}",
    }
