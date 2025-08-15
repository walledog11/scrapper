# depop_scraper_lib.py
# Hybrid scraper:
#  - Search page via HTTP (requests) to avoid grid timeouts on Streamlit Cloud
#  - Optional detail enrichment via Playwright (price/size/condition/title)
#  - Safe fallbacks so the UI never crashes

import asyncio, time, random, re, urllib.parse, os
from typing import List, Dict, Tuple, Optional

import requests
from requests.adapters import HTTPAdapter, Retry


# ------------------------------
# Public API
# ------------------------------
def scrape_depop(term: str, deep: bool, limits: dict) -> List[Dict]:
    """
    Hybrid flow:
      1) Use HTTP to fetch the search page HTML and extract product links quickly.
      2) If deep=True, open each product in Playwright (concurrently) to extract
         price, size, condition, and a clean title/brand.
      3) If Playwright can't launch in this environment, keep the HTTP rows;
         if even HTTP yields nothing, return a sample row to keep UI responsive.
    """
    # 1) HTTP search
    max_items = int(limits.get("MAX_ITEMS", 300))
    max_seconds = int(limits.get("MAX_DURATION_S", 600))

    links = http_find_product_links(term, max_items=max_items, max_seconds=max_seconds)

    # Construct base rows from the slug while we (optionally) enrich
    rows = [row_from_link(term, link) for link in links]

    # 2) Optional deep details via Playwright
    if deep and rows:
        try:
            details = asyncio.run(playwright_enrich(rows, limits))
            # merge back
            by_link = {d["link"]: d for d in details}
            for r in rows:
                full = by_link.get(r["link"])
                if not full:
                    continue
                # Only fill missing fields or upgrade empties
                for k in ["brand", "item_name", "price", "size", "condition"]:
                    if full.get(k) and not r.get(k):
                        r[k] = full[k]
        except Exception as e:
            print(f"[WARN] Deep enrichment failed; keeping HTTP rows. {e}")

    # 3) Final fallback
    if not rows:
        rows = [_sample_row(term)]

    # Deduplicate by link
    dedup = {}
    for r in rows:
        if r.get("link"):
            dedup[r["link"]] = r
    return list(dedup.values())


# ------------------------------
# HTTP search helpers
# ------------------------------
def _requests_session() -> requests.Session:
    s = requests.Session()
    retries = Retry(
        total=3, backoff_factor=0.4,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"])
    )
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://www.depop.com/",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    })
    return s


PRODUCT_HREF_RE = re.compile(r'href="(/products/[^"#?]+)"')


def http_find_product_links(term: str, max_items: int = 300, max_seconds: int = 600) -> List[str]:
    """
    Request the Depop search HTML and pull product links via regex.
    This works even if the grid is JS-hydrated: many links exist in initial markup.
    """
    base_url = "https://www.depop.com/search/?"
    q = urllib.parse.urlencode({"q": term, "sort": "relevance", "country": "us"})
    url = base_url + q

    session = _requests_session()

    start = time.time()
    links: List[str] = []
    seen = set()

    try:
        resp = session.get(url, timeout=15)
        html = resp.text or ""
    except Exception as e:
        print(f"[WARN] HTTP search failed: {e}")
        return []

    for m in PRODUCT_HREF_RE.finditer(html):
        href = m.group(1)
        if href in seen:
            continue
        seen.add(href)
        full = f"https://www.depop.com{href}"
        links.append(full)
        if len(links) >= max_items:
            break

    # if we found nothing, try a second pass: look for quoted URLs via loose regex
    if not links:
        ALT_RE = re.compile(r'["\'](/products/[^"\']+)["\']')
        for m in ALT_RE.finditer(html):
            href = m.group(1)
            if href in seen:
                continue
            seen.add(href)
            full = f"https://www.depop.com{href}"
            links.append(full)
            if len(links) >= max_items:
                break

    # Respect max_seconds (even though HTTP is quick)
    if time.time() - start > max_seconds:
        links = links[:max_items]

    return links


def row_from_link(term: str, link: str) -> Dict:
    """
    Create a shallow row from a product link by deriving a reasonable title/brand
    from the slug. Real details may be filled by the deep step.
    """
    slug = link.rstrip("/").split("/")[-1]
    title_guess = slug.replace("-", " ").strip()

    # Heuristic: remove very short tokens that look like seller names prefix/suffix
    parts = [p for p in title_guess.split() if len(p) > 1]
    clean_title = " ".join(parts).strip()

    return {
        "platform": "Depop",
        "brand": "",
        "item_name": clean_title or term,
        "price": "",
        "size": "",
        "condition": "",
        "link": link,
    }


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


# ------------------------------
# Playwright enrichment (details)
# ------------------------------
async def playwright_enrich(rows: List[Dict], limits: dict) -> List[Dict]:
    """
    Visit detail pages to extract price/size/condition/title/brand.
    Launches Playwright once and fetches in parallel (limited).
    """
    from playwright.async_api import async_playwright

    conc = max(1, int(limits.get("DEEP_FETCH_CONCURRENCY", 3)))
    delay_min = int(limits.get("DEEP_FETCH_DELAY_MIN", 600))
    delay_max = int(limits.get("DEEP_FETCH_DELAY_MAX", 1400))
    limit = int(limits.get("DEEP_FETCH_MAX", len(rows)))

    targets = rows[:limit]

    out: List[Dict] = []

    async with async_playwright() as p:
        browser = None
        launch_args = [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-background-networking",
        ]
        # Prefer Chromium; fall back to Firefox
        try:
            browser = await p.chromium.launch(headless=True, args=launch_args)
        except Exception as e:
            print(f"[WARN] Chromium launch failed: {e}")
            try:
                browser = await p.firefox.launch(headless=True)
            except Exception as e2:
                print(f"[ERROR] Firefox launch failed: {e2}")
                # No browsers—return originals
                return rows

        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            timezone_id=os.environ.get("TZ", "America/Los_Angeles"),
        )

        sem = asyncio.Semaphore(conc)

        async def one(row: Dict):
            async with sem:
                page = await ctx.new_page()
                try:
                    await page.goto(row["link"], wait_until="commit", timeout=60_000)
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=20_000)
                    except Exception:
                        pass
                    brand, title, price, size, condition = await _extract_detail_fields(page)
                    out.append({
                        "platform": "Depop",
                        "brand": brand or row.get("brand", ""),
                        "item_name": title or row.get("item_name", ""),
                        "price": price or row.get("price", ""),
                        "size": size or row.get("size", ""),
                        "condition": condition or row.get("condition", ""),
                        "link": row["link"],
                    })
                except Exception as e:
                    print(f"[WARN] detail fetch failed for {row.get('link')}: {e}")
                    out.append(row.copy())
                finally:
                    try:
                        await page.close()
                    except Exception:
                        pass
                # polite random delay to avoid hammering
                await asyncio.sleep(random.uniform(delay_min/1000, delay_max/1000))

        await asyncio.gather(*(one(r) for r in targets))

        try:
            await ctx.close()
            await browser.close()
        except Exception:
            pass

    # Preserve order by original rows
    by_link = {r["link"]: r for r in out}
    merged = []
    for r in rows:
        merged.append(by_link.get(r["link"], r))
    return merged


async def _extract_detail_fields(page) -> Tuple[str, str, str, str, str]:
    """
    Extract brand, title, price, size, condition from a product detail page.
    Uses resilient selector list + regex fallbacks.
    """
    js = r"""
    () => {
      const pick = (arr) => {
        for (const sel of arr) {
          const el = document.querySelector(sel);
          if (el) return (el.textContent || "").trim();
        }
        return "";
      };
      const title = pick([
        "[data-testid='product-title']",
        "h1", "h2"
      ]);
      const brand = pick([
        "[data-testid='brand']",
        "a[href*='/brand/']",
        "span[class*='brand']",
        "a[aria-label*='Brand']"
      ]);
      let price = pick([
        "[data-testid='price']",
        "[itemprop='price']",
        "span[aria-label*='Price']",
        "div[aria-label*='Price']",
        "span[class*='Price']",
        "p[class*='Price']"
      ]);
      if (!price) {
        const m = (document.body.innerText || "").match(/[£$€]\s?\d+[.,]?\d*/);
        if (m) price = m[0];
      }
      // Size/Condition (try common label/value pairs)
      let size = "";
      let condition = "";

      const text = (n) => (n && (n.textContent || "").trim()) || "";

      // look for definition lists
      const dts = Array.from(document.querySelectorAll("dt, div, span, p"));
      for (const el of dts) {
        const t = text(el).toLowerCase();
        if (!size && t.includes("size")) {
          // sibling or next element
          const sib = el.nextElementSibling;
          if (sib) { size = text(sib); }
        }
        if (!condition && t.includes("condition")) {
          const sib = el.nextElementSibling;
          if (sib) { condition = text(sib); }
        }
      }

      // Clean title if it has separators (avoid seller mix-ins)
      let cleanTitle = title;
      if (cleanTitle && (cleanTitle.includes("•") || cleanTitle.includes("|"))) {
        const parts = cleanTitle.split(/•|\|/).map(s => s.trim()).filter(Boolean);
        if (parts.length) parts.sort((a,b) => b.length - a.length), cleanTitle = parts[0];
      }

      return { brand, title: cleanTitle, price, size, condition };
    }
    """
    try:
        data = await page.evaluate(js)
        return (
            data.get("brand", "") or "",
            data.get("title", "") or "",
            data.get("price", "") or "",
            data.get("size", "") or "",
            data.get("condition", "") or "",
        )
    except Exception:
        return ("", "", "", "", "")
