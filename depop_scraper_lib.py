# depop_scraper_lib.py
import os, sys, subprocess, asyncio, random, time, urllib.parse, glob
from typing import List, Dict, Optional

PLAYWRIGHT_CACHE = os.path.expanduser("~/.cache/ms-playwright")
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", PLAYWRIGHT_CACHE)

# ----------------- helpers -----------------
def _run(cmd, note=""):
    """Run a command; never raise. Return (rc, out, err)."""
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        out, err = p.communicate()
        rc = p.returncode
        if rc != 0:
            print(f"[WARN] {note} rc={rc}\nstdout:\n{out[:2000]}\nstderr:\n{err[:2000]}")
        else:
            if out:
                print(f"[INFO] {note} stdout:\n{out[:1000]}")
        return rc, out, err
    except Exception as e:
        print(f"[WARN] {_short(cmd)} failed: {e}")
        return 1, "", str(e)

def _short(cmd):
    try:
        return " ".join(cmd[:4]) + (" ..." if len(cmd) > 4 else "")
    except:
        return str(cmd)

def _find_binary(engine: str) -> Optional[str]:
    """Locate a browser binary in the Playwright cache."""
    patterns = []
    if engine == "chromium":
        patterns = [
            os.path.join(PLAYWRIGHT_CACHE, "chromium_headless_shell-*", "chrome-linux", "headless_shell"),
            os.path.join(PLAYWRIGHT_CACHE, "chromium-*", "chrome-linux", "chrome"),
        ]
    elif engine == "firefox":
        patterns = [
            os.path.join(PLAYWRIGHT_CACHE, "firefox-*", "firefox", "firefox"),
        ]
    for pat in patterns:
        for m in sorted(glob.glob(pat)):
            if os.path.isfile(m) and os.access(m, os.X_OK):
                return m
    return None

def ensure_browser(engine: str) -> Optional[str]:
    """
    Best-effort install for Playwright engines.
    Returns executable path if found, else None.
    Never raises on installer errors—just logs.
    """
    # 0) Already present?
    bin_path = _find_binary(engine)
    if bin_path:
        print(f"[INFO] Found {engine} binary at: {bin_path}")
        return bin_path

    # 1) Try install-deps (no-op on Streamlit if packages.txt handled it)
    _run([sys.executable, "-m", "playwright", "install-deps", engine], note=f"install-deps {engine}")

    # 2) Try regular install with deps
    _run([sys.executable, "-m", "playwright", "install", engine, "--with-deps"], note=f"install {engine} --with-deps")

    # 3) Force re-download
    if not _find_binary(engine):
        _run([sys.executable, "-m", "playwright", "install", engine, "--force"], note=f"install {engine} --force")

    bin_path = _find_binary(engine)
    if bin_path:
        print(f"[INFO] After install, found {engine} binary at: {bin_path}")
    else:
        print(f"[WARN] {engine} binary still not found after install attempts (continuing).")
    return bin_path

# ----------------- public API -----------------
def scrape_depop(term: str, deep: bool, limits: dict) -> List[Dict]:
    """
    Sync wrapper. Tries real scrape with Playwright; falls back to sample rows if unavailable.
    """
    try:
        from playwright.async_api import async_playwright  # noqa: F401
    except Exception:
        # Fallback so the app stays usable (helps verify Sheets wiring in cloud)
        return [{
            "platform": "Depop",
            "brand": "Supreme",
            "item_name": f"{term} (sample)",
            "price": "$199",
            "size": "L",
            "condition": "Good condition",
            "link": f"https://www.depop.com/search/?q={urllib.parse.quote_plus(term)}",
        }]

    return asyncio.run(_scrape_depop_async(term, deep, limits))

# ----------------- detail-page extractor JS -----------------
DETAIL_EXTRACT_JS = r"""
(() => {
  const clean = s => (s || "").replace(/\s+/g,' ').trim();

  // Helpers
  const q = sel => document.querySelector(sel);
  const qt = sel => { const el = q(sel); return el ? clean(el.textContent) : ""; };
  const getAttrStarts = (attr, prefix) => {
    for (const el of document.querySelectorAll(`[${attr}]`)) {
      const v = el.getAttribute(attr) || "";
      if (v.toLowerCase().startsWith(prefix.toLowerCase())) return clean(v);
    }
    return "";
  };

  const pickGranularCondition = (text) => {
    if (!text) return "";
    const m = text.match(/\b(brand\s*new|new with tags|new without tags|excellent|very\s*good|good|fair|poor)\s+condition\b/i);
    return m ? clean(m[0]) : "";
  };

  const prettySchemaCondition = (val) => {
    if (!val) return "";
    const s = String(val).trim();
    const slug = s.startsWith("http") ? s.split("/").pop() : s;
    const map = {
      NewCondition: "Brand New",
      UsedCondition: "Used",
      RefurbishedCondition: "Refurbished",
      DamagedCondition: "Damaged",
    };
    return map[slug] || s;
  };

  // 1) DOM guesses
  let title = qt('h1,[data-testid="listing-title"],[itemprop="name"]') || document.title || "";
  let price = qt('[data-testid="price"],[itemprop="price"],[aria-label*="Price"]') || "";
  let size  =
    qt('[data-testid="size"]') ||
    qt('button[aria-pressed="true"], button[aria-selected="true"]') ||
    getAttrStarts('aria-label', 'size') ||
    "";
  if (size && /^size[:\s]/i.test(size)) size = clean(size.replace(/^size[:\s]*/i,''));

  let condition =
    qt('[data-testid="condition"]') ||
    qt('[itemprop="itemCondition"]') ||
    "";

  // 2) <dt>/<dd> fallbacks
  if (!size || size.length > 16) {
    const dts = Array.from(document.querySelectorAll('dt, .dt, [role="term"]'));
    for (const dt of dts) {
      const t = clean(dt.textContent).toLowerCase();
      if (t.startsWith('size')) {
        const dd = dt.nextElementSibling;
        if (dd) { const txt = clean(dd.textContent); if (txt) { size = txt; break; } }
      }
    }
  }
  if (!condition || /^https?:\/\//.test(condition)) {
    const dts = Array.from(document.querySelectorAll('dt, .dt, [role="term"]'));
    for (const dt of dts) {
      const t = clean(dt.textContent).toLowerCase();
      if (t.startsWith('condition')) {
        const dd = dt.nextElementSibling;
        if (dd) { const txt = clean(dd.textContent); if (txt) { condition = txt; break; } }
      }
    }
  }

  // 3) JSON sources: __NEXT_DATA__ and ld+json
  const parseJSONSafe = t => { try { return JSON.parse(t); } catch { return null; } };
  const crawlFor = (root, keys) => {
    const seen = new Set(), stack = [root];
    while (stack.length) {
      const cur = stack.pop();
      if (!cur || typeof cur !== 'object' || seen.has(cur)) continue;
      seen.add(cur);
      for (const [k, v] of Object.entries(cur)) {
        const lk = k.toLowerCase();
        if (keys.includes(lk)) {
          if (typeof v === 'string' && v.trim()) return v.trim();
          if (typeof v === 'number') return String(v);
          if (v && typeof v === 'object') {
            const cand = v.name || v.value || v.label || v.text || v['@id'];
            if (typeof cand === 'string' && cand.trim()) return String(cand).trim();
          }
        }
        if (v && typeof v === 'object') stack.push(v);
      }
      if (Array.isArray(cur)) for (const it of cur) stack.push(it);
    }
    return "";
  };

  const nextScript = document.querySelector('#__NEXT_DATA__');
  const nextData = nextScript && nextScript.textContent ? parseJSONSafe(nextScript.textContent) : null;

  if ((!size || size.length > 16) && nextData) {
    size = crawlFor(nextData, ["size","selectedsize","itemsize","sizelabel","variant"]);
    if (size && /^size[:\s]/i.test(size)) size = clean(size.replace(/^size[:\s]*/i,''));
  }
  if ((!condition || /^https?:\/\//.test(condition)) && nextData) {
    const raw = crawlFor(nextData, ["condition","itemcondition","productcondition","conditionlabel","conditiontext","itemCondition"]);
    condition = prettySchemaCondition(raw) || condition;
  }

  const ldBlocks = Array.from(document.querySelectorAll('script[type="application/ld+json"]'))
    .map(s => s.textContent ? parseJSONSafe(s.textContent) : null)
    .filter(Boolean);

  if ((!condition || /^https?:\/\//.test(condition)) && ldBlocks.length) {
    for (const b of ldBlocks) {
      const raw = crawlFor(b, ["condition","itemcondition","productcondition","itemCondition"]);
      if (raw) { condition = prettySchemaCondition(raw); break; }
    }
  }
  if ((!size || size.length > 16) && ldBlocks.length) {
    for (const b of ldBlocks) {
      const s = crawlFor(b, ["size","itemsize","sizelabel"]);
      if (s) { size = s; break; }
    }
  }

  // 4) Text fallback across the whole page
  const body = document.body ? document.body.innerText : "";
  if (!size || size.length > 16) {
    const m = body.match(/\b(?:size|sz)\s*[:\-]?\s*([A-Za-z0-9./\-]{1,8})\b/i);
    if (m && m[1]) size = clean(m[1]);
  }

  // Prefer granular phrases over plain "Used"
  const granular = pickGranularCondition(body);
  if (granular) condition = granular;

  // Normalize “Size: L…”
  if (size && /^size[:\s]/i.test(size)) size = clean(size.replace(/^size[:\s]*/i,''));

  return {
    title: clean(title),
    price: clean(price),
    size: clean(size),
    condition: clean(condition),
  };
})()
"""

# ----------------- real scraper -----------------
async def _scrape_depop_async(term: str, deep: bool, limits: dict) -> List[Dict]:
    from playwright.async_api import async_playwright

    base_url = f"https://www.depop.com/search/?q={urllib.parse.quote_plus(term)}"
    max_items = int(limits.get("MAX_ITEMS", 500))
    max_seconds = int(limits.get("MAX_DURATION_S", 600))

    # Try Chromium first; if not available, fall back to Firefox.
    chromium_bin = ensure_browser("chromium")
    use_engine = "chromium" if chromium_bin else "firefox"
    if use_engine == "firefox":
        firefox_bin = ensure_browser("firefox")
        if not firefox_bin:
            print("[ERROR] No Playwright browsers available. Returning sample row.")
            return [{
                "platform": "Depop",
                "brand": "Supreme",
                "item_name": f"{term} (sample)",
                "price": "$199",
                "size": "L",
                "condition": "Good condition",
                "link": f"https://www.depop.com/search/?q={urllib.parse.quote_plus(term)}",
            }]

    # Cloud-safe flags for Chromium; Firefox ignores unsupported flags
    launch_args = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-background-networking",
    ]

    async with async_playwright() as p:
        # Choose engine and executable_path when we found one
        if use_engine == "chromium":
            browser_type = p.chromium
            exe = chromium_bin
        else:
            browser_type = p.firefox
            exe = _find_binary("firefox")  # may be None (Playwright manages internally)

        # Launch
        try:
            if exe:
                browser = await browser_type.launch(headless=True, args=launch_args, executable_path=exe)
            else:
                browser = await browser_type.launch(headless=True, args=launch_args)
        except Exception as e:
            print(f"[ERROR] Launch failed for {use_engine}: {e}")
            return [{
                "platform": "Depop",
                "brand": "Supreme",
                "item_name": f"{term} (sample)",
                "price": "$199",
                "size": "L",
                "condition": "Good condition",
                "link": f"https://www.depop.com/search/?q={urllib.parse.quote_plus(term)}",
            }]

        ctx = await browser.new_context(
            user_agent=("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
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
                    ps = await page.evaluate(
                        "el => Array.from(el.querySelectorAll('p')).map(n => n.textContent || '')", li
                    )
                    for t in ps:
                        if any(sym in (t or "") for sym in ["$", "£", "€"]):
                            price = (t or "").strip()
                    for t in reversed(ps):
                        s = (t or "").strip()
                        if s and s != price and len(s) <= 40:
                            brand = s
                            break

                slug = href.rstrip("/").split("/")[-1].replace("-", " ")
                item_name = slug
                if brand and slug.lower().startsWith(brand.lower()):
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

            await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            await page.wait_for_timeout(random.randint(500, 1000))

        # If no deep fetch requested, close & return list-level rows
        if not deep or not rows:
            await browser.close()
            return rows

        # ------------- Deep fetch for Size/Condition -------------
        # Limits with sensible defaults
        deep_max = int(limits.get("DEEP_FETCH_MAX", min(1200, len(rows))))
        concurrency = int(limits.get("DEEP_FETCH_CONCURRENCY", 3))
        delay_min = int(limits.get("DEEP_FETCH_DELAY_MIN", 800))
        delay_max = int(limits.get("DEEP_FETCH_DELAY_MAX", 1600))

        # Unique links
        by_link = {r["link"]: r for r in rows}
        links = list(by_link.keys())[:deep_max]

        sem = asyncio.Semaphore(concurrency)

        async def _deep_fetch_one(page, link: str) -> Dict:
            await page.goto(link, wait_until="domcontentloaded", timeout=60000)
            try:
                await page.wait_for_selector("#__NEXT_DATA__", timeout=4000)
            except Exception:
                pass
            await page.evaluate("window.scrollBy(0, document.body.scrollHeight * 0.25)")
            await page.wait_for_timeout(random.randint(delay_min, delay_max))
            try:
                data = await page.evaluate(DETAIL_EXTRACT_JS)
            except Exception:
                data = {}
            base = by_link[link]
            return {
                "platform": "Depop",
                "brand": base.get("brand",""),
                "item_name": data.get("title") or base.get("item_name",""),
                "price": data.get("price") or base.get("price",""),
                "size": data.get("size") or base.get("size",""),
                "condition": data.get("condition") or base.get("condition",""),
                "link": link,
            }

        async def worker(batch: List[str]) -> List[Dict]:
            out = []
            page_local = await ctx.new_page()
            try:
                for link in batch:
                    async with sem:
                        row = await _deep_fetch_one(page_local, link)
                        out.append(row)
            finally:
                await page_local.close()
            return out

        batches = [links[i::concurrency] for i in range(concurrency) if links[i::concurrency]]
        results_nested = await asyncio.gather(*(worker(b) for b in batches), return_exceptions=True)

        final_rows: List[Dict] = []
        for chunk in results_nested:
            if isinstance(chunk, list):
                final_rows.extend([r for r in chunk if isinstance(r, dict)])

        await browser.close()
        return final_rows
