# depop_scraper_lib.py
# Robust + faster Depop scraper helpers for Streamlit app
# - No brittle waits on a single selector
# - Blocks heavy resources to speed up scrolling
# - Derives list titles from slug; upgrades to canonical product title on deep-fetch
# - Optional deep fetch enriches size/condition (NEXT_DATA + DOM fallbacks)

import asyncio, random, time, urllib.parse
from typing import List, Dict

# ----------------- Public API -----------------
def scrape_depop(term: str, deep: bool, limits: dict) -> List[Dict]:
    """
    Sync wrapper. Tries real scrape with Playwright; returns a sample row if Playwright is missing.
    """
    try:
        from playwright.async_api import async_playwright  # noqa: F401
    except Exception:
        # Fallback so the app stays usable (helps verify Sheets wiring)
        return [{
            "platform": "Depop",
            "brand": "Sample",
            "item_name": f"{term} (sample)",
            "price": "$199",
            "size": "L",
            "condition": "Good condition",
            "link": f"https://www.depop.com/search/?q={urllib.parse.quote_plus(term)}",
        }]

    return asyncio.run(_scrape_depop_async(term, deep, limits))


# ----------------- Internal helpers -----------------
PRODUCT_SELECTORS = [
    "a[href^='/products/']",
    "[href^='/products/']",
    "a[data-testid='product-card']",
    "[data-testid='product-card'] a",
]

async def _enable_fast_mode(ctx):
    """Block heavy/irrelevant resources to speed up scrolling."""
    async def _block(route):
        r = route.request
        rt = r.resource_type
        url = r.url
        if rt in ("image", "media", "font", "stylesheet"):
            return await route.abort()
        # block analytics/script noise
        if any(d in url for d in (
            "googletagmanager.com", "google-analytics.com", "doubleclick.net", "facebook.net"
        )):
            return await route.abort()
        return await route.continue_()
    await ctx.route("**/*", _block)

async def _wait_for_any_products(page, min_count=6, timeout_ms=25000):
    """Poll for any product anchors across multiple selectors; no 'visible' requirement."""
    start = time.time()
    while (time.time() - start) * 1000 < timeout_ms:
        total = 0
        for sel in PRODUCT_SELECTORS:
            try:
                els = await page.query_selector_all(sel)
                total += len(els)
            except Exception:
                pass
        if total >= min_count:
            return True
        await page.wait_for_timeout(300)
    return False

async def _collect_hrefs(page):
    """Return a deduped list of '/products/...' hrefs currently in the DOM."""
    hrefs = set()
    for sel in PRODUCT_SELECTORS:
        try:
            anchors = await page.query_selector_all(sel)
            for a in anchors:
                href = await a.get_attribute("href")
                if href and href.startswith("/products/"):
                    hrefs.add(href.rstrip("/"))
        except Exception:
            pass
    return list(hrefs)


# ----------------- Real scraper -----------------
async def _scrape_depop_async(term: str, deep: bool, limits: dict) -> List[Dict]:
    from playwright.async_api import async_playwright

    # Config with safe defaults
    base_url = f"https://www.depop.com/search/?q={urllib.parse.quote_plus(term)}"
    max_items = int(limits.get("MAX_ITEMS", 500))
    max_seconds = int(limits.get("MAX_DURATION_S", 600))
    pause_min = int(limits.get("PAUSE_MIN", 500))
    pause_max = int(limits.get("PAUSE_MAX", 900))

    # Launch Chromium headless with cloud-safe flags
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-background-networking",
            ],
        )
        ctx = await browser.new_context(
            user_agent=("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
        )
        await _enable_fast_mode(ctx)

        page = await ctx.new_page()
        await page.goto(base_url, wait_until="domcontentloaded", timeout=60000)

        # Non-blocking cookie/consent accept
        try:
            for sel in (
                "button:has-text('Accept')",
                "button:has-text('Accept all')",
                "[data-testid='cookie-accept']",
                "text=Accept cookies",
            ):
                btn = await page.query_selector(sel)
                if btn:
                    await btn.click()
                    break
        except Exception:
            pass

        # Don’t hard-fail if this times out; we scroll regardless
        await _wait_for_any_products(page, min_count=6, timeout_ms=25000)

        start = time.time()
        seen = set()
        rows: List[Dict] = []

        while True:
            # Grab whatever is currently rendered
            hrefs = await _collect_hrefs(page)
            growth = 0

            for href in hrefs:
                if href in seen:
                    continue
                seen.add(href)

                # Derive a readable title from slug (guaranteed to be just the listing words)
                slug = href.split("/")[-1].replace("-", " ").strip()
                title = slug

                # Try to collect price + a short brand-like label from nearby text
                price = ""
                brand = ""
                try:
                    a = await page.query_selector(f"a[href='{href}'], a[href^='{href}']")
                    if a:
                        li = await a.evaluate_handle("el => el.closest('li') || el.parentElement")
                        if li:
                            texts = await page.evaluate(
                                "el => Array.from(el.querySelectorAll('p,span,div'))"
                                ".map(n => (n.textContent||'').trim()).filter(Boolean)", li
                            )
                            # Price-looking token
                            for t in texts:
                                if any(sym in t for sym in ("$", "£", "€")) and any(ch.isdigit() for ch in t):
                                    price = t
                                    break
                            # Short non-price token as brand (avoid usernames like '@foo')
                            for t in reversed(texts):
                                if t != price and 1 <= len(t.split()) <= 3 and len(t) <= 32 and not t.startswith("@"):
                                    brand = t
                                    break
                except Exception:
                    pass

                rows.append({
                    "platform": "Depop",
                    "brand": brand,
                    "item_name": title,         # list title from slug only (no seller/user)
                    "price": price or "N/A",
                    "size": "",
                    "condition": "",
                    "link": f"https://www.depop.com{href}",
                })
                growth += 1

                if len(rows) >= max_items:
                    break

            # Stop conditions
            if len(rows) >= max_items:
                break
            if time.time() - start > max_seconds:
                break

            # Scroll to load more cards
            await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            await page.wait_for_timeout(random.randint(pause_min, pause_max))

            # Short opportunistic idle (don’t block long)
            try:
                await page.wait_for_load_state("networkidle", timeout=2000)
            except Exception:
                pass

            # If we’re not growing after a while, bail early
            if growth == 0 and (time.time() - start) > (max_seconds * 0.6):
                break

        # ---------- Optional deep fetch for Size / Condition ----------
        if deep and rows:
            detail_max = int(limits.get("DEEP_FETCH_MAX", 300))
            delay_min = int(limits.get("DEEP_FETCH_DELAY_MIN", 800))
            delay_max = int(limits.get("DEEP_FETCH_DELAY_MAX", 1600))
            conc = int(limits.get("DEEP_FETCH_CONCURRENCY", 3))

            targets = rows[:detail_max]
            by_link = {r["link"]: r for r in rows}
            sem = asyncio.Semaphore(conc)

            async def fetch_detail(url):
                async with sem:
                    p = await ctx.new_page()
                    try:
                        await p.goto(url, wait_until="domcontentloaded", timeout=45000)
                        await p.wait_for_timeout(random.randint(delay_min, delay_max))
                        details = await p.evaluate("""
                        () => {
                          const clean = s => (s||'').replace(/\\s+/g,' ').trim();
                          const get = sel => (document.querySelector(sel)?.textContent || '').trim();
                          const parseJSONSafe = t => { try { return JSON.parse(t) } catch { return null } };

                          // Try __NEXT_DATA__ first
                          const nd = document.querySelector('#__NEXT_DATA__');
                          const next = nd && nd.textContent ? parseJSONSafe(nd.textContent) : null;

                          // Scan object graphs for likely keys
                          function firstString(obj, keys){
                            const stack=[obj], seen=new Set();
                            while(stack.length){
                              const cur=stack.pop();
                              if(!cur || typeof cur!=='object') continue;
                              if(seen.has(cur)) continue;
                              seen.add(cur);
                              for(const k of Object.keys(cur)){
                                const v = cur[k]; const lk = k.toLowerCase();
                                if(keys.includes(lk)){
                                  if(typeof v==='string' && v.trim()) return v.trim();
                                  if(v && typeof v==='object'){
                                    const cand = v.name||v.value||v.label||v.text;
                                    if(typeof cand==='string' && cand.trim()) return cand.trim();
                                  }
                                }
                                if(v && typeof v==='object') stack.push(v);
                              }
                            }
                            return '';
                          }

                          let size = '';
                          let condition = '';

                          // dt/dd pairs in the DOM
                          const dts = Array.from(document.querySelectorAll('dt, .dt, [role="term"]'));
                          for(const dt of dts){
                            const t = clean(dt.textContent).toLowerCase();
                            if(!size && t.startsWith('size')){
                              const dd = dt.nextElementSibling;
                              if(dd){ size = clean(dd.textContent) }
                            }
                            if(!condition && t.startsWith('condition')){
                              const dd = dt.nextElementSibling;
                              if(dd){ condition = clean(dd.textContent) }
                            }
                          }

                          if(next){
                            if(!size) size = firstString(next, ["size","itemsize","sizelabel","selectedsize","variant"]);
                            if(!condition) condition = firstString(next, ["condition","itemcondition","conditionlabel","conditiontext"]);
                          }

                          const title = get('h1,[data-testid="listing-title"],[itemprop="name"]') || '';
                          let price  = get('[data-testid="price"],[itemprop="price"], [aria-label*="Price"], span[aria-label*="Price"]');

                          return { title: clean(title), price: clean(price), size: clean(size), condition: clean(condition) };
                        }
                        """)
                        if details:
                            rec = by_link.get(url)
                            if rec:
                                if details.get("title"):
                                    rec["item_name"] = details["title"]  # canonical product title
                                if details.get("price"):
                                    rec["price"] = details["price"]
                                if details.get("size"):
                                    rec["size"] = details["size"]
                                if details.get("condition"):
                                    rec["condition"] = details["condition"]
                    finally:
                        await p.close()

            await asyncio.gather(*(fetch_detail(r["link"]) for r in targets))

        await browser.close()
        return rows
