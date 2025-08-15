# depop_scraper_lib.py
import os, sys, asyncio, time, random
from typing import List, Dict, Optional
import urllib.parse
import aiohttp

# ---------- Tunables ----------
HTTP_TIMEOUT_S = 20
RETRY_MAX = 3
RETRY_BACKOFF = (0.8, 2.0)  # min/max backoff seconds
PAGE_SIZE = 100  # Depop API: 24/50/100 typically work; adjust if needed

# ---------- Public API ----------
def scrape_depop(term: str, deep: bool, limits: dict) -> List[Dict]:
    """
    Wrapper. If the caller asked to use HTTP fallback OR Playwright can't launch,
    we use the HTTP/JSON strategy (fast, cloud-friendly).
    """
    use_http = bool(limits.get("USE_REQUESTS_FALLBACK", True))

    if not use_http:
        # Try Playwright path; if it fails, auto-fallback to HTTP.
        try:
            return asyncio.run(_scrape_with_playwright(term, deep, limits))
        except Exception as e:
            print(f"[WARN] Playwright failed: {e}. Falling back to HTTP.")
            use_http = True

    if use_http:
        try:
            return asyncio.run(_scrape_via_http(term, deep, limits))
        except Exception as e:
            print(f"[ERROR] HTTP fallback failed: {e}. Returning sample row as last resort.")
            return [_sample_row(term)]

    # Should not reach here
    return [_sample_row(term)]


# ---------- Sample row fallback ----------
def _sample_row(term: str) -> Dict:
    return {
        "platform": "Depop",
        "brand": "",
        "item_name": f"{term} (sample)",
        "price": "",
        "size": "",
        "condition": "",
        "link": f"https://www.depop.com/search/?q={urllib.parse.quote_plus(term)}",
    }


# ---------- HTTP JSON strategy (fast & cloud friendly) ----------
async def _scrape_via_http(term: str, deep: bool, limits: dict) -> List[Dict]:
    """
    Uses Depop's public endpoints to fetch search results quickly and reliably.
    Then (optionally) visits detail endpoints to extract size/condition.
    """
    max_items = int(limits.get("MAX_ITEMS", 500))
    max_seconds = int(limits.get("MAX_DURATION_S", 900))
    deep_cap = int(limits.get("DEEP_FETCH_MAX", 300))
    deep_conc = int(limits.get("DEEP_FETCH_CONCURRENCY", 3))
    delay_min = int(limits.get("DEEP_FETCH_DELAY_MIN", 400))
    delay_max = int(limits.get("DEEP_FETCH_DELAY_MAX", 1200))

    ts_start = time.time()
    items: List[Dict] = []

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.depop.com/",
        "Origin": "https://www.depop.com",
        # No auth/cookies required for basic search
    }

    # Known search endpoint pattern (public). If Depop changes this,
    # tweak the base URL & param names below.
    base = "https://webapi.depop.com/api/v2/search/products/"

    async def fetch_json(session: aiohttp.ClientSession, url: str) -> Optional[dict]:
        for attempt in range(1, RETRY_MAX + 1):
            try:
                async with session.get(url, timeout=HTTP_TIMEOUT_S) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    # 404/5xx retryable
                    await asyncio.sleep(random.uniform(*RETRY_BACKOFF))
            except Exception as e:
                # transient network error
                await asyncio.sleep(random.uniform(*RETRY_BACKOFF))
        return None

    params = {
        "q": term,
        "limit": PAGE_SIZE,  # 100 results per page
        # Add more filters if you want (country, price range, sort, etc.)
    }

    async with aiohttp.ClientSession(headers=headers) as session:
        offset = 0
        while len(items) < max_items and (time.time() - ts_start) < max_seconds:
            # Build URL with pagination
            q = {**params, "offset": offset}
            url = base + "?" + urllib.parse.urlencode(q)
            data = await fetch_json(session, url)
            if not data:
                break

            products = data.get("products") or data.get("data") or []
            if not products:
                break

            for p in products:
                # Fields vary; extract safely
                # Common: id, price, currency, slug, brand, description/title, etc.
                pid = p.get("id")
                price_val = ""
                # Price may be nested or split
                if isinstance(p.get("price"), dict):
                    amount = p["price"].get("price_amount") or p["price"].get("amount") or p["price"].get("value")
                    cur = p["price"].get("currency") or ""
                    if amount:
                        # Depop often gives integer cents; normalize if needed
                        try:
                            amt = float(amount)
                            if amt > 1000 and str(amount).isdigit():
                                amt = amt / 100.0
                            price_val = f"{cur} {amt}".strip()
                        except Exception:
                            price_val = f"{cur} {amount}".strip()
                else:
                    # Fallback
                    raw = p.get("price") or ""
                    cur = p.get("currency") or ""
                    if raw:
                        price_val = f"{cur} {raw}".strip()

                # Title/name
                title = (
                    p.get("name")
                    or p.get("description")
                    or p.get("slug")
                    or ""
                ).strip()

                # Brand (varies)
                brand = ""
                if isinstance(p.get("brand"), dict):
                    brand = (p["brand"].get("name") or "").strip()
                else:
                    brand = (p.get("brand") or "").strip()

                # Link—prefer slug if present
                slug = p.get("slug")
                if slug:
                    link = f"https://www.depop.com/products/{slug}/"
                else:
                    # fallback to ID-based path
                    link = f"https://www.depop.com/products/{pid}/"

                items.append({
                    "platform": "Depop",
                    "brand": brand,
                    "item_name": title,
                    "price": price_val,
                    "size": "",
                    "condition": "",
                    "link": link,
                    "_id": pid,  # keep for detail fetch
                })

                if len(items) >= max_items:
                    break

            # move to next page
            offset += PAGE_SIZE

    # Optional deep fetch for size/condition
    if deep and items:
        # We’ll fetch details for the first N items (DEEP_FETCH_MAX)
        detail_targets = [it for it in items if it.get("_id")][:deep_cap]

        sem = asyncio.Semaphore(deep_conc)

        async def fetch_detail(session: aiohttp.ClientSession, it: Dict):
            await sem.acquire()
            try:
                pid = it.get("_id")
                if not pid:
                    return
                url = f"https://webapi.depop.com/api/v2/products/{pid}/"
                for attempt in range(1, RETRY_MAX + 1):
                    try:
                        async with session.get(url, timeout=HTTP_TIMEOUT_S) as resp:
                            if resp.status == 200:
                                d = await resp.json()
                                # Size
                                size_val = ""
                                # Common patterns:
                                # - 'attributes' list with name/value
                                # - 'size' or 'size_label' fields
                                attrs = d.get("attributes") or []
                                for a in attrs:
                                    n = (a.get("name") or "").lower()
                                    v = (a.get("value") or "").strip()
                                    if n in ("size", "size label", "size_label") and v:
                                        size_val = v
                                        break
                                if not size_val:
                                    size_val = (d.get("size") or d.get("size_label") or "").strip()

                                # Condition
                                cond = ""
                                # Often 'condition' is buried in seller listings text/enum
                                cond = (d.get("condition") or d.get("item_condition") or "").strip()
                                if not cond and isinstance(d.get("attributes"), list):
                                    for a in d["attributes"]:
                                        n = (a.get("name") or "").lower()
                                        v = (a.get("value") or "").strip()
                                        if n in ("condition", "item condition") and v:
                                            cond = v
                                            break

                                if size_val:
                                    it["size"] = size_val
                                if cond:
                                    it["condition"] = cond
                                return
                            await asyncio.sleep(random.uniform(*RETRY_BACKOFF))
                    except Exception:
                        await asyncio.sleep(random.uniform(*RETRY_BACKOFF))
            finally:
                # jitter to be nice
                await asyncio.sleep(random.uniform(delay_min/1000.0, delay_max/1000.0))
                sem.release()

        headers2 = dict(headers)
        async with aiohttp.ClientSession(headers=headers2) as session:
            tasks = [fetch_detail(session, it) for it in detail_targets]
            await asyncio.gather(*tasks, return_exceptions=True)

    # Clean up & drop helper key
    for it in items:
        it.pop("_id", None)

    return items


# ---------- Playwright path (optional) ----------
async def _scrape_with_playwright(term: str, deep: bool, limits: dict) -> List[Dict]:
    """
    Your original Playwright logic could go here if you still want it.
    For reliability on Streamlit Cloud, the HTTP path above is recommended.
    """
    # If you want to keep a very small Playwright fallback, return the sample row
    # to avoid crashes on environments with missing libs.
    try:
        from playwright.async_api import async_playwright  # noqa: F401
    except Exception:
        return [_sample_row(term)]

    # You can implement your previous playwright logic here if desired.
    # Given cloud constraints, we return a sample to keep the UI responsive.
    return [_sample_row(term)]
