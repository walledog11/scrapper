# depop_scraper_lib.py
import asyncio, random, time, urllib.parse, os
from typing import List, Dict

# ------------------------------
# Public API
# ------------------------------
def scrape_depop(term: str, deep: bool, limits: dict) -> List[Dict]:
    """
    Sync wrapper around the async scraper. If Playwright isn't available or
    no browser can be launched in the environment, returns a single sample row
    so the Streamlit UI remains responsive.
    """
    try:
        from playwright.async_api import async_playwright  # noqa: F401
    except Exception:
        # Playwright not installed / import failed — return a friendly sample row
        return [_sample_row(term)]

    try:
        return asyncio.run(_scrape_depop_async(term, deep, limits))
    except Exception as e:
        # Final guard — never crash the UI
        print(f"[ERROR] scrape_depop() failed: {e}")
        return [_sample_row(term)]


# ------------------------------
# Internals
# ------------------------------

def _sample_row(term: str) -> Dict:
    return {
        "platform": "Depop",
        "brand": "Sample",
        "item_name": f"{term} (sample result)",
        "price": "$199",
        "size": "L",
        "condition": "Good",
        "link": f"https://www.depop.com/search/?q={urllib.parse.quote_plus(term)}",
    }


async def _goto_with_retries(page, url: str, max_tries: int = 4) -> None:
    """
    Navigate with progressive backoff and explicit stabilization.
    This avoids hanging on 'domcontentloaded' for slow/throttled pages.
    """
    last_err = None
    for attempt in range(1, max_tries + 1):
        try:
            # 'commit' returns once the main request commits.
            await page.goto(url, wait_until="commit", timeout=60_000)

            # Best-effort stabilization
            for state, to in (("domcontentloaded", 20_000), ("networkidle", 20_000)):
                try:
                    await page.wait_for_load_state(state, timeout=to)
                except Exception:
                    pass
            # readyState complete (best-effort)
            try:
                await page.wait_for_function(
                    "() => document.readyState === 'complete'", timeout=15_000
                )
            except Exception:
                pass
            return
        except Exception as e:
            last_err = e
            # jittered backoff: 0.8s, 1.6s, 3.2s, 4.8s
            delay = min(6.0, 0.8 * (2 ** (attempt - 1)))
            try:
                await page.wait_for_timeout(int(delay * 1000))
            except Exception:
                pass
    raise last_err if last_err else RuntimeError("Navigation failed")


async def _wait_for_any_selector(page, selectors: list[str], timeout_ms: int = 45_000) -> None:
    """
    Wait for any of the given selectors to appear (attached).
    We loop in short slices across all candidates so we don't spend the full
    timeout on a single selector.
    """
    slice_timeout = max(3_000, int(timeout_ms / max(3, len(selectors))))
    end_time = time.time() + (timeout_ms / 1000.0)
    last_err = None

    while time.time() < end_time:
        for sel in selectors:
            try:
                await page.wait_for_selector(sel, state="attached", timeout=slice_timeout)
                return
            except Exception as e:
                last_err = e
        # short breather
        try:
            await page.wait_for_timeout(300)
        except Exception:
            pass

    if last_err:
        raise last_err
    raise TimeoutError("None of the selectors appeared")


async def _scrape_depop_async(term: str, deep: bool, limits: dict) -> List[Dict]:
    """
    Actual crawler:
      - Navigates robustly to the search URL (with retries & stabilization)
      - Waits for any of several known product-card selectors (A/B test proof)
      - Collects product links and shallow card data (brand/title/price when present)
      - Optionally deep-fetches item pages for Size/Condition (when deep=True)
    """
    from playwright.async_api import async_playwright

    base_url = (
        "https://www.depop.com/search/?"
        + urllib.parse.urlencode(
            {
                "q": term,
                "sort": "relevance",
                "country": "us",
            }
        )
    )

    max_items = int(limits.get("MAX_ITEMS", 300))
    max_seconds = int(limits.get("MAX_DURATION_S", 600))

    product_selectors = [
        "a[data-testid='product-card']",
        "a[href^='/products/']",
        "[data-testid='product-tile'] a[href^='/products/']",
    ]

    async with async_playwright() as p:
        # Try launching Chromium; if it fails, try Firefox. If both fail, return sample.
        browser = None
        launch_args = [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-background-networking",
        ]

        # Prefer chromium
        try:
            browser = await p.chromium.launch(headless=True, args=launch_args)
        except Exception as e:
            print(f"[WARN] Chromium launch failed: {e}")
            try:
                browser = await p.firefox.launch(headless=True)
            except Exception as e2:
                print(f"[ERROR] Firefox launch failed: {e2}")
                return [_sample_row(term)]

        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            timezone_id=os.environ.get("TZ", "America/Los_Angeles"),
        )
        page = await ctx.new_page()
        page.set_default_timeout(60_000)
        page.set_default_navigation_timeout(90_000)

        # Navigate robustly
        await _goto_with_retries(page, base_url, max_tries=4)

        # Gentle nudge to trigger lazy grid load
        try:
            await page.evaluate("window.scrollBy(0, 800)")
            await page.wait_for_timeout(700)
            await page.evaluate("window.scrollTo(0, 0)")
            await page.wait_for_timeout(400)
        except Exception:
            pass

        # Wait for ANY product card selector
        await _wait_for_any_selector(page, product_selectors, timeout_ms=60_000)

        start_time = time.time()
        rows: List[Dict] = []
        seen_links = set()

        # Collect product tiles (we'll loop + scroll until caps)
        while True:
            # Grab anchors (combine selectors)
            anchors = []
            for sel in product_selectors:
                try:
                    anchors.extend(await page.query_selector_all(sel))
                except Exception:
                    pass

            for a in anchors:
                try:
                    href = await a.get_attribute("href")
                    if not href:
                        continue
                    # Normalize link
                    if href.startswith("/"):
                        link = f"https://www.depop.com{href}"
                    else:
                        if href.startswith("http"):
                            link = href
                        else:
                            # Just in case a relative path without leading slash
                            link = f"https://www.depop.com/{href.lstrip('/')}"
                    if link in seen_links:
                        continue
                    seen_links.add(link)

                    # Extract a best-effort title/brand/price from within the tile (shallow)
                    brand, title, price = await _extract_from_tile(page, a)

                    rows.append(
                        {
                            "platform": "Depop",
                            "brand": brand or "",
                            "item_name": title or "",
                            "price": price or "",
                            "size": "",
                            "condition": "",
                            "link": link,
                        }
                    )
                    if len(rows) >= max_items:
                        break
                except Exception:
                    # Skip problematic nodes; keep scraping
                    pass

            # Stop conditions
            if len(rows) >= max_items:
                break
            if time.time() - start_time > max_seconds:
                break

            # Scroll & let new cards load
            try:
                await page.evaluate("window.scrollBy(0, window.innerHeight * 1.1)")
                await page.wait_for_timeout(random.randint(500, 1000))
            except Exception:
                break

            # If we haven't grown in a while, consider we're at the end
            if len(rows) >= max_items or time.time() - start_time > max_seconds:
                break

        # Optional deep fetch for size/condition
        if deep and rows:
            limit = int(limits.get("DEEP_FETCH_MAX", 250))
            conc = max(1, int(limits.get("DEEP_FETCH_CONCURRENCY", 3)))
            delay_min = int(limits.get("DEEP_FETCH_DELAY_MIN", 800))
            delay_max = int(limits.get("DEEP_FETCH_DELAY_MAX", 1600))
            await _deep_fill_details(ctx, rows[:limit], conc, delay_min, delay_max)

        await browser.close()
        # Deduplicate by link (just in case)
        dedup: Dict[str, Dict] = {}
        for r in rows:
            if r.get("link"):
                dedup[r["link"]] = r
        return list(dedup.values())


async def _extract_from_tile(page, tile) -> tuple[str, str, str]:
    """
    Pull brand / title / price from a product tile node.
    Works even if classes/attributes shift (we try multiple probes).
    """
    js = """
    (node) => {
      const txt = (sel) => {
        const el = node.querySelector(sel);
        return el ? (el.textContent || "").trim() : "";
      };
      // Title candidates (avoid seller name by preferring data attributes / aria labels)
      let title =
        node.getAttribute("aria-label") ||
        txt("[data-testid='product-title']") ||
        txt("[data-testid='title']") ||
        txt("h3, h2") ||
        "";

      // Brand often shows up as a small text element; keep short tokens only
      let brand =
        txt("[data-testid='brand']") ||
        txt("p[class*='brand'], span[class*='brand']") ||
        "";

      // Price candidates
      let price =
        txt("[data-testid='price']") ||
        txt("[itemprop='price']") ||
        txt("span[aria-label*='Price'], div[aria-label*='Price']") ||
        txt("span[class*='Price'], p[class*='Price']") ||
        "";

      // Fallback price via regex on tile text
      if (!price) {
        const m = (node.innerText || "").match(/[£$€]\s?\d+[.,]?\d*/);
        if (m) price = m[0];
      }

      // Clean up combined "Seller • Title" patterns by splitting on bullets or pipes
      if (title && (title.includes("•") || title.includes("|"))) {
        const parts = title.split(/•|\|/).map(s => s.trim()).filter(Boolean);
        // Pick the longest part as item title (heuristic)
        if (parts.length) {
          parts.sort((a,b) => b.length - a.length);
          title = parts[0];
        }
      }

      return {brand, title, price};
    }
    """
    try:
        data = await page.evaluate(js, tile)
        return (data.get("brand", ""), data.get("title", ""), data.get("price", ""))
    except Exception:
        # Last resort: empty fields; let deep fetch fill details
        return ("", "", "")


async def _deep_fill_details(ctx, rows: List[Dict], concurrency: int, delay_min_ms: int, delay_max_ms: int):
    """
    Visit item pages to extract Size / Condition, and (if missing) Price and Title.
    Concurrency is controlled with simple semaphores to avoid hammering the site.
    """
    sem = asyncio.Semaphore(concurrency)

    async def one(row: Dict):
        async with sem:
            page = await ctx.new_page()
            try:
                await page.goto(row["link"], wait_until="commit", timeout=60_000)
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=20_000)
                except Exception:
                    pass
                # Extract details from detail page
                brand, title, price, size, condition = await _extract_detail_fields(page)
                if brand and not row.get("brand"):
                    row["brand"] = brand
                if title and not row.get("item_name"):
                    row["item_name"] = title
                if price and not row.get("price"):
                    row["price"] = price
                if size:
                    row["size"] = size
                if condition:
                    row["condition"] = condition
            except Exception as e:
                print(f"[WARN] detail fetch failed for {row.get('link')}: {e}")
            finally:
                try:
                    await page.close()
                except Exception:
                    pass
            # polite delay
            try:
                await asyncio.sleep(random.uniform(delay_min_ms/1000, delay_max_ms/1000))
            except Exception:
                pass

    await asyncio.gather(*(one(r) for r in rows))


async def _extract_detail_fields(page) -> tuple[str, str, str, str, str]:
    """
    Extract brand, title, price, size, condition from a product detail page with resilient selectors.
    """
    js = """
    () => {
      const txt = (sel) => {
        const el = document.querySelector(sel);
        return el ? (el.textContent || "").trim() : "";
      };

      // Title candidates
      let title =
        txt("[data-testid='product-title']") ||
        txt("h1") || txt("h2") || "";

      // Brand candidates (sometimes near the title)
      let brand =
        txt("[data-testid='brand']") ||
        txt("a[href*='/brand/']") ||
        txt("span[class*='brand']") || "";

      // Price with fallback regex
      let price =
        txt("[data-testid='price']") ||
        txt("[itemprop='price']") ||
        txt("span[aria-label*='Price'], div[aria-label*='Price']") ||
        txt("span[class*='Price'], p[class*='Price']") || "";
      if (!price) {
        const m = (document.body.innerText || "").match(/[£$€]\s?\\d+[.,]?\\d*/);
        if (m) price = m[0];
      }

      // Size candidates
      let size =
        txt("[data-testid='size']") ||
        txt("dt:contains('Size') + dd") ||
        txt("div:has(> span:contains('Size')) span:last-child") || "";

      // Condition candidates
      let condition =
        txt("[data-testid='condition']") ||
        txt("dt:contains('Condition') + dd") ||
        txt("div:has(> span:contains('Condition')) span:last-child") || "";

      // Clean title like before if it has separators
      if (title && (title.includes("•") || title.includes("|"))) {
        const parts = title.split(/•|\\|/).map(s => s.trim()).filter(Boolean);
        if (parts.length) {
          parts.sort((a,b) => b.length - a.length);
          title = parts[0];
        }
      }

      return {brand, title, price, size, condition};
    }
    """
    try:
        data = await page.evaluate(js)
        return (
            data.get("brand", ""),
            data.get("title", ""),
            data.get("price", ""),
            data.get("size", ""),
            data.get("condition", ""),
        )
    except Exception:
        return ("", "", "", "", "")
