# depop_scraper_lib.py
import asyncio, random, time, urllib.parse
from typing import List, Dict, Tuple

SEARCH_URL = "https://www.depop.com/search/?q={q}"

# --------------------- JavaScript snippets ---------------------

# Collect listing href + rough title + rough price from search results
LIST_COLLECT_JS = r"""
(() => {
  const clean = s => (s || "").replace(/\s+/g, " ").trim();
  const anchors = Array.from(document.querySelectorAll('a[href^="/products/"]'));
  const seen = new Set();
  const out = [];

  function extractPriceFromCard(card) {
    if (!card) return "";
    // 1) Explicit price nodes first
    let node = card.querySelector('[data-testid="price"], [itemprop="price"], [class*="price"]');
    if (node) {
      const t = clean(node.textContent);
      if (/[£$€]\s?\d/.test(t)) return t;
    }
    // 2) Any short text inside the card that looks like a currency
    const texts = Array.from(card.querySelectorAll("p,span,div,strong"))
      .map(n => clean(n.textContent))
      .filter(Boolean);
    const hit = texts.find(t => /[£$€]\s?\d/.test(t));
    if (hit) return hit;

    return "";
  }

  function extractTitleFromAnchor(a) {
    let title = a.getAttribute("aria-label") || "";
    if (!title) {
      const card = a.closest("article, li, div") || a.parentElement;
      if (card) {
        const texts = Array.from(card.querySelectorAll("h3,h2,strong,p,span"))
          .map(n => clean(n.textContent))
          .filter(Boolean);
        const pick = texts.find(t =>
          !/[£$€]\s?\d/.test(t) && !/^by\s+/i.test(t) && t.length >= 3 && t.length <= 120
        );
        if (pick) title = pick;
      }
      if (!title) {
        const slug = (a.getAttribute("href") || "").replace(/\/$/, "").split("/").pop() || "";
        title = clean(decodeURIComponent(slug.replace(/-/g, " ")));
      }
    }
    // strip " | seller" or " by seller"
    const lower = title.toLowerCase();
    if (lower.includes(" by ")) title = title.split(/ by /i)[0].trim();
    if (title.includes(" | ")) title = title.split(" | ")[0].trim();
    return title;
  }

  for (const a of anchors) {
    const href = a.getAttribute("href");
    if (!href || seen.has(href)) continue;
    seen.add(href);

    const card = a.closest("article, li, div") || a.parentElement;
    const title = extractTitleFromAnchor(a);
    let price = extractPriceFromCard(card);

    // Fallback: some builds include price in the anchor aria-label
    if (!price) {
      const al = a.getAttribute("aria-label") || "";
      const m = (al || "").match(/[£$€]\s?\d[\d.,]*/);
      if (m) price = m[0];
    }

    out.push({ href, title, price });
  }
  return out;
})()
"""

# Extract title/price/size/condition on the PDP
DETAIL_EXTRACT_JS = r"""
(() => {
  const clean = s => (s || "").replace(/\s+/g,' ').trim();
  const txt = sel => {
    const el = document.querySelector(sel);
    return el ? clean(el.textContent) : "";
  };

  function findTitle() {
    return (
      txt('h1,[data-testid="listing-title"],[itemprop="name"]') ||
      document.title.replace(/\s*\|\s*Depop.*$/i, "").trim() ||
      ""
    );
  }

  function findPrice() {
    // A) JSON-LD (Product -> offers.price)
    try {
      const blocks = Array.from(document.querySelectorAll('script[type="application/ld+json"]'))
        .map(n => { try { return JSON.parse(n.textContent || "null"); } catch { return null; }})
        .filter(Boolean);
      for (const b of blocks) {
        const arr = Array.isArray(b) ? b : [b];
        for (const obj of arr) {
          if (obj && (obj["@type"] === "Product" || obj["@type"] === "Offer" || obj.productID)) {
            const offers = obj.offers || obj.offer || null;
            const p = offers && (offers.price || (offers.priceSpecification && offers.priceSpecification.price));
            const cur = (offers && (offers.priceCurrency || (offers.priceSpecification && offers.priceSpecification.priceCurrency))) || "";
            if (p) {
              const sym = cur === "USD" ? "$" : cur === "GBP" ? "£" : cur === "EUR" ? "€" : "";
              return (sym ? `${sym}${p}` : `${p}`).trim();
            }
          }
        }
      }
    } catch (e) {}

    // B) Meta tags / microdata
    const metaPrice = document.querySelector('meta[itemprop="price"], meta[property="product:price:amount"], meta[property="og:price:amount"]');
    const metaCur   = document.querySelector('meta[itemprop="priceCurrency"], meta[property="product:price:currency"], meta[property="og:price:currency"]');
    if (metaPrice) {
      const p = metaPrice.getAttribute("content") || "";
      const c = metaCur ? (metaCur.getAttribute("content") || "") : "";
      if (p) {
        const sym = c === "USD" ? "$" : c === "GBP" ? "£" : c === "EUR" ? "€" : "";
        return (sym ? `${sym}${p}` : p).trim();
      }
    }

    // C) Visible nodes
    const nodes = [
      '[data-testid="price"]',
      '[itemprop="price"]',
      'div[aria-label*="Price"]',
      'span[aria-label*="Price"]',
      'span[class*="price"]',
      'div[class*="price"]'
    ];
    for (const sel of nodes) {
      const el = document.querySelector(sel);
      if (el) {
        const t = clean(el.textContent);
        if (/[£$€]\s?\d/.test(t)) return t;
      }
    }

    // D) Body text fallback
    const m = (document.body.innerText || "").match(/[£$€]\s?\d[\d.,]*/);
    return m ? m[0] : "";
  }

  function findSize() {
    const sel = [
      '[data-testid="size"]',
      'button[aria-pressed="true"]',
      'button[aria-selected="true"]',
      '[class*="chip"][aria-pressed="true"]',
      '[class*="chip"][aria-selected="true"]'
    ];
    for (const s of sel) {
      const el = document.querySelector(s);
      if (el) {
        const t = clean(el.textContent);
        if (t && t.length <= 20) return t;
      }
    }
    // Definition lists
    const dts = Array.from(document.querySelectorAll('dt, .dt, [role="term"]'));
    for (const dt of dts) {
      const k = clean(dt.textContent).toLowerCase();
      if (k.startsWith("size")) {
        const dd = dt.nextElementSibling;
        if (dd) {
          const v = clean(dd.textContent);
          if (v) return v;
        }
      }
    }
    // Text fallback
    const m = (document.body.innerText || "").match(/\b(?:size|sz)\s*[:\-]?\s*([A-Za-z0-9./\- ]{1,12})/i);
    return (m && m[1]) ? clean(m[1]) : "";
  }

  function findCondition() {
    const dts = Array.from(document.querySelectorAll('dt, .dt, [role="term"]'));
    for (const dt of dts) {
      const k = clean(dt.textContent).toLowerCase();
      if (k.startsWith("condition")) {
        const dd = dt.nextElementSibling;
        if (dd) {
          const v = clean(dd.textContent);
          if (v) return v;
        }
      }
    }
    const blocks = Array.from(document.querySelectorAll('script[type="application/ld+json"]'))
      .map(n => { try { return JSON.parse(n.textContent || "null"); } catch { return null; }})
      .filter(Boolean);
    for (const b of blocks) {
      const m = JSON.stringify(b).match(/(NewCondition|UsedCondition|RefurbishedCondition|DamagedCondition)/);
      if (m && m[1]) {
        const map = {NewCondition:"Brand New", UsedCondition:"Used", RefurbishedCondition:"Refurbished", DamagedCondition:"Damaged"};
        return map[m[1]] || m[1];
      }
    }
    const body = document.body.innerText || "";
    const m = body.match(/\b(brand\s*new|new with tags|new without tags|excellent|very good|good|fair|poor)\s+condition\b/i);
    return (m && m[0]) ? clean(m[0]) : "";
  }

  return { title: findTitle(), price: findPrice(), size: findSize(), condition: findCondition() };
})()
"""

# --------------------- Python helpers ---------------------

def _full_url(href: str) -> str:
  if href.startswith("http"):
      return href
  return "https://www.depop.com" + (href if href.startswith("/") else "/" + href)

async def _collect_listings(page, max_items: int, max_seconds: int, log) -> List[Dict]:
    start = time.time()
    seen_links = set()
    rows: List[Dict] = []

    while True:
        batch = await page.evaluate(LIST_COLLECT_JS)
        added = 0
        for it in batch:
            link = _full_url(it.get("href", ""))
            if not link or link in seen_links:
                continue
            seen_links.add(link)

            raw_title = (it.get("title") or "").strip()
            title = raw_title
            lower = raw_title.lower()
            if " by " in lower:
                title = raw_title.split(" by ", 1)[0].strip()
            if " | " in title:
                title = title.split(" | ", 1)[0].strip()

            price = (it.get("price") or "").strip()

            rows.append({
                "platform": "Depop",
                "brand": "",
                "item_name": title,
                "price": price or "",   # may be filled during deep fetch
                "size": "",
                "condition": "",
                "link": link,
            })
            added += 1
            if len(rows) >= max_items:
                break

        log(f"Collected: {len(rows)} (+{added})")
        if len(rows) >= max_items or (time.time() - start) > max_seconds:
            break

        await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
        await page.wait_for_timeout(random.randint(450, 900))
        if added == 0:
            try:
                await page.wait_for_load_state("networkidle", timeout=4000)
            except Exception:
                pass

    return rows

async def _deep_fetch(context, links: List[str], base_by_link: Dict[str, Dict], delay_ms: Tuple[int,int], sem, log):
    page = await context.new_page()
    try:
        for link in links:
            async with sem:
                try:
                    await page.goto(link, wait_until="domcontentloaded", timeout=60000)
                    await page.evaluate("window.scrollBy(0, document.body.scrollHeight * 0.2)")
                    await page.wait_for_timeout(random.randint(*delay_ms))
                    details = await page.evaluate(DETAIL_EXTRACT_JS)
                except Exception as e:
                    log(f"Detail error: {e}")
                    details = {}

                base = base_by_link.get(link, {})
                # Only overwrite if new value is non-empty
                if details.get("title"):      base["item_name"] = details["title"]
                if details.get("price"):      base["price"] = details["price"]
                if details.get("size"):       base["size"] = details["size"]
                if details.get("condition"):  base["condition"] = details["condition"]
    finally:
        await page.close()

# --------------------- Public API ---------------------

def scrape_depop(term: str, deep: bool, limits: dict) -> List[Dict]:
    """
    Sync entry for app.py. Falls back to a sample row if Playwright is missing.
    """
    try:
        from playwright.async_api import async_playwright  # noqa: F401
    except Exception:
        return [{
            "platform": "Depop",
            "brand": "",
            "item_name": f"{term} (sample)",
            "price": "",
            "size": "",
            "condition": "",
            "link": f"https://www.depop.com/search/?q={urllib.parse.quote_plus(term)}",
        }]

    return asyncio.run(_scrape_depop_async(term, deep, limits))

async def _scrape_depop_async(term: str, deep: bool, limits: dict) -> List[Dict]:
    from playwright.async_api import async_playwright

    base_url = SEARCH_URL.format(q=urllib.parse.quote_plus(term))
    max_items = int(limits.get("MAX_ITEMS", 1000))
    max_seconds = int(limits.get("MAX_DURATION_S", 900))

    deep_max = int(limits.get("DEEP_FETCH_MAX", 800))
    deep_conc = int(limits.get("DEEP_FETCH_CONCURRENCY", 3))
    dmin = int(limits.get("DEEP_FETCH_DELAY_MIN", 800))
    dmax = int(limits.get("DEEP_FETCH_DELAY_MAX", 1600))
    delay_ms = (dmin, dmax)

    launch_args = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-background-networking",
        "--disable-features=Translate,BackForwardCache,AvoidUnnecessaryBeforeUnloadCheckSync",
    ]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=launch_args)
        ctx = await browser.new_context(
            user_agent=("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
        )
        page = await ctx.new_page()

        def log(m): print(m)

        log(f"Opening: {base_url}")
        await page.goto(base_url, wait_until="domcontentloaded", timeout=60000)

        # Try both selectors before we start scrolling
        try:
            await page.wait_for_selector('a[href^="/products/"]', state="attached", timeout=15000)
        except Exception:
            await page.wait_for_selector("a[data-testid='product-card']", state="attached", timeout=20000)

        # Cookie banner (best-effort)
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

        rows = await _collect_listings(page, max_items=max_items, max_seconds=max_seconds, log=log)

        if deep and rows:
            by_link = {r["link"]: r for r in rows}
            links = list(by_link.keys())[:deep_max]
            log(f"Deep fetching {len(links)} items…")

            sem = asyncio.Semaphore(deep_conc)
            batches = [links[i::deep_conc] for i in range(deep_conc)]
            tasks = [_deep_fetch(ctx, b, by_link, delay_ms, sem, log) for b in batches if b]
            await asyncio.gather(*tasks, return_exceptions=True)

        await browser.close()
        return rows
