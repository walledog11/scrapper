# depop_scraper_lib.py — resilient grid wait, robust price/title, safe fallback
import os, sys, asyncio, time, random, re, glob, urllib.parse, subprocess
from typing import List, Dict, Optional

PLAYWRIGHT_CACHE = os.path.expanduser("~/.cache/ms-playwright")
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", PLAYWRIGHT_CACHE)

def _run(cmd, note=""):
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        out, err = p.communicate()
        if p.returncode != 0:
            print(f"[WARN] {note} rc={p.returncode}\n{err[:800]}")
        return p.returncode, out, err
    except Exception as e:
        print(f"[WARN] {_short(cmd)} failed: {e}")
        return 1, "", str(e)

def _short(cmd): 
    try: return " ".join(cmd[:4]) + (" ..." if len(cmd) > 4 else "")
    except: return str(cmd)

def _find_bin(engine: str) -> Optional[str]:
    pats = []
    if engine == "chromium":
        pats = [
            os.path.join(PLAYWRIGHT_CACHE, "chromium_headless_shell-*", "chrome-linux", "headless_shell"),
            os.path.join(PLAYWRIGHT_CACHE, "chromium-*", "chrome-linux", "chrome"),
        ]
    elif engine == "firefox":
        pats = [os.path.join(PLAYWRIGHT_CACHE, "firefox-*", "firefox", "firefox")]
    for pat in pats:
        for m in sorted(glob.glob(pat)):
            if os.path.isfile(m) and os.access(m, os.X_OK):
                return m
    return None

def ensure_browser(engine: str) -> Optional[str]:
    p = _find_bin(engine)
    if p: 
        print(f"[INFO] Found {engine} at {p}")
        return p
    _run([sys.executable, "-m", "playwright", "install-deps", engine], f"install-deps {engine}")
    _run([sys.executable, "-m", "playwright", "install", engine, "--with-deps"], f"install {engine} --with-deps")
    if not _find_bin(engine):
        _run([sys.executable, "-m", "playwright", "install", engine, "--force"], f"install {engine} --force")
    return _find_bin(engine)

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def _clean_title(s: str) -> str:
    s = _norm(s)
    s = s.split(" | ", 1)[0]
    s = re.sub(r"\s+by\s+.+$", "", s, flags=re.I)
    return s

def _normalize_price(raw: str) -> str:
    s = (raw or "").strip()
    if not s: return ""
    m = re.search(r"([£$€])\s?(\d[\d.,]*)", s)
    if m:
        sym, amt = m.group(1), m.group(2).replace(",", "")
        return f"{sym}{amt}"
    m2 = re.search(r"(USD|GBP|EUR)\s?(\d[\d.,]*)", s, re.I)
    if m2:
        sym = {"USD":"$", "GBP":"£", "EUR":"€"}.get(m2.group(1).upper(), "")
        amt = m2.group(2).replace(",", "")
        return f"{sym}{amt}".strip()
    m3 = re.search(r"(\d[\d.,]*)", s)
    if m3:
        amt = m3.group(1).replace(",", "")
        return f"${amt}"
    return ""

def _is_product_link(href: str) -> bool:
    return bool(href) and "/products/" in href

def _sample(term: str) -> List[Dict]:
    return [{
        "platform": "Depop",
        "brand": "Sample",
        "item_name": f"{term} (sample)",
        "price": "$199",
        "size": "L",
        "condition": "Good condition",
        "link": f"https://www.depop.com/search/?q={urllib.parse.quote_plus(term)}",
    }]

# ----------------- Public API -----------------
def scrape_depop(term: str, deep: bool, limits: dict) -> List[Dict]:
    try:
        from playwright.async_api import async_playwright  # noqa: F401
    except Exception:
        return _sample(term)
    return asyncio.run(_scrape_depop_async(term, deep, limits))

# ----------------- Real scraper -----------------
async def _scrape_depop_async(term: str, deep: bool, limits: dict) -> List[Dict]:
    from playwright.async_api import async_playwright

    base_url = f"https://www.depop.com/search/?q={urllib.parse.quote_plus(term)}&sort=relevance&country=us"
    max_items    = int(limits.get("MAX_ITEMS", 300))
    max_seconds  = int(limits.get("MAX_DURATION_S", 60))
    deep_max     = int(limits.get("DEEP_FETCH_MAX", 200))

    # Browser
    chromium_bin = ensure_browser("chromium")
    engine = "chromium" if chromium_bin else "firefox"
    if engine == "firefox" and not ensure_browser("firefox"):
        print("[ERROR] No browsers available; using sample.")
        return _sample(term)

    launch_args = ["--no-sandbox","--disable-setuid-sandbox","--disable-dev-shm-usage","--disable-gpu","--disable-background-networking"]

    async with async_playwright() as p:
        bt = p.chromium if engine == "chromium" else p.firefox
        exe = chromium_bin if engine == "chromium" else _find_bin("firefox")
        try:
            browser = await bt.launch(headless=True, args=launch_args, executable_path=exe) if exe else await bt.launch(headless=True, args=launch_args)
        except Exception as e:
            print(f"[ERROR] Launch failed: {e}")
            return _sample(term)

        ctx = await browser.new_context(user_agent=("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"))
        page = await ctx.new_page()
        await page.goto(base_url, wait_until="domcontentloaded", timeout=60000)

        # Accept cookies if shown
        try:
            for sel in ["button:has-text('Accept')", "button:has-text('Accept all')", "[data-testid='cookie-accept']", "text=Accept cookies"]:
                btn = await page.query_selector(sel)
                if btn:
                    await btn.click()
                    break
        except Exception:
            pass

        # Robust wait for any product card
        product_selectors = [
            "a[href^='/products/']",
            "a[data-testid='product-card']",
            "[data-testid='productCard'] a",
            "article a[href*='/products/']",
        ]
        found_any = False
        t0 = time.time()
        while time.time() - t0 < 25:
            for sel in product_selectors:
                try:
                    await page.wait_for_selector(sel, state="attached", timeout=1200)
                    found_any = True
                    break
                except Exception:
                    continue
            if found_any:
                break
            await page.evaluate("window.scrollBy(0, 900)")
            await page.wait_for_timeout(500)
        if not found_any:
            # last visible attempt
            try:
                await page.wait_for_selector(product_selectors[0], state="visible", timeout=8000)
                found_any = True
            except Exception:
                pass
        if not found_any:
            print("[WARN] No product selector matched; returning sample.")
            await browser.close()
            return _sample(term)

        async def _extract_from_card(a) -> Optional[Dict]:
            href = await a.get_attribute("href")
            if not _is_product_link(href):
                return None

            # text on card
            texts = []
            try:
                texts = await page.evaluate("""(el) => Array.from(el.querySelectorAll('img[alt], p, span, div'))
                                               .map(n => (n.alt || n.textContent || '').trim())
                                               .filter(Boolean)""", a)
            except Exception:
                pass

            # price: first currency-like
            price = ""
            for t in texts:
                m = re.search(r"[£$€]\s?\d[\d.,]*", t)
                if m:
                    price = m.group(0)
                    break

            # title: prefer image alt
            title = ""
            try:
                img = await a.query_selector("img[alt]")
                if img:
                    alt = await img.get_attribute("alt")
                    if alt and alt.strip():
                        title = alt.strip()
            except Exception:
                pass
            if not title:
                non_price = [t for t in texts if not re.search(r"[£$€]\s?\d", t)]
                if non_price:
                    title = non_price[0]

            return {
                "platform": "Depop",
                "brand": "",  # fill via PDP if deep
                "item_name": _clean_title(title),
                "price": _normalize_price(price),
                "size": "",
                "condition": "",
                "link": f"https://www.depop.com{href}",
            }

        # scroll & collect
        rows: List[Dict] = []
        seen = set()
        start = time.time()
        while True:
            anchors = []
            for sel in product_selectors:
                try:
                    anchors = await page.query_selector_all(sel)
                    if anchors: break
                except Exception:
                    continue

            for a in anchors:
                href = await a.get_attribute("href")
                if not _is_product_link(href) or href in seen: 
                    continue
                seen.add(href)
                item = await _extract_from_card(a)
                if item:
                    rows.append(item)
                if len(rows) >= max_items: break

            if len(rows) >= max_items: break
            if time.time() - start > max_seconds: break

            await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            await page.wait_for_timeout(random.randint(450, 900))

        # PDP deep fill (price/size/condition/title/brand)
        if deep and rows:
            filled = 0
            for r in rows:
                if filled >= deep_max: break
                link = r.get("link")
                if not _is_product_link(link): continue
                try:
                    dp = await ctx.new_page()
                    await dp.goto(link, wait_until="domcontentloaded", timeout=60000)
                    try:
                        await dp.wait_for_selector("main, article, [data-testid='product-page']", state="attached", timeout=8000)
                    except Exception:
                        pass

                    DETAIL_JS = """
                    (function() {
                      const pick = (sel, attr) => {
                        const el = document.querySelector(sel);
                        if (!el) return "";
                        return attr ? (el.getAttribute(attr) || "").trim()
                                    : (el.textContent || "").trim();
                      };

                      // Price with robust fallbacks
                      let price = pick('meta[itemprop="price"]','content') ||
                                  pick('[itemprop="price"]') ||
                                  pick('[data-testid="price"]') ||
                                  pick('div[aria-label*="Price" i]') ||
                                  pick('span[aria-label*="Price" i]') ||
                                  pick('p[class*="price" i]') ||
                                  pick('span[class*="price" i]');
                      if (!price) {
                        const m = (document.body.innerText || "").match(/[£$€]\\s?\\d+[.,]?\\d*/);
                        if (m) price = m[0];
                      }

                      let title = pick('h1') || pick('[data-testid="product-title"]') || "";
                      let brand = pick('[data-testid="brand"]') || "";

                      let size = "", condition = "";
                      const dts = Array.from(document.querySelectorAll('dt'));
                      for (const dt of dts) {
                        const key = (dt.textContent || "").toLowerCase().trim();
                        const dd = dt.nextElementSibling;
                        const val = dd ? (dd.textContent || "").trim() : "";
                        if (!val) continue;
                        if (!size && key.includes("size")) size = val;
                        if (!condition && (key.includes("condition") || key.includes("state"))) condition = val;
                      }
                      size = size || pick('[data-testid="product-size"]') || "";
                      condition = condition || pick('[data-testid="product-condition"]') || "";

                      return { price, title, brand, size, condition };
                    })();
                    """
                    info = await dp.evaluate(DETAIL_JS)

                    if info:
                        if info.get("title"):     r["item_name"] = _clean_title(info["title"])
                        if info.get("brand"):     r["brand"] = _norm(info["brand"])
                        if info.get("size"):      r["size"] = _norm(info["size"])
                        if info.get("condition"): r["condition"] = _norm(info["condition"])
                        pp = _normalize_price(info.get("price", ""))
                        if pp: r["price"] = pp

                    if not r.get("price"):
                        try:
                            txt = await dp.inner_text("body", timeout=2000)
                            m = re.search(r"[£$€]\s?\d[\d.,]*", txt)
                            if m:
                                r["price"] = _normalize_price(m.group(0))
                        except Exception:
                            pass
                except Exception as e:
                    print(f"[WARN] PDP fail {link}: {e}")
                finally:
                    try: await dp.close()
                    except Exception: pass
                filled += 1

        await browser.close()

        # finalize & dedupe
        out: List[Dict] = []
        seen_links = set()
        for r in rows:
            lk = r.get("link","")
            if not _is_product_link(lk) or lk in seen_links:
                continue
            seen_links.add(lk)
            out.append({
                "platform": "Depop",
                "brand": _norm(r.get("brand","")),
                "item_name": _clean_title(r.get("item_name","")),
                "price": _normalize_price(r.get("price","")),
                "size": _norm(r.get("size","")),
                "condition": _norm(r.get("condition","")),
                "link": lk,
            })
        print(f"[INFO] Collected {len(out)} items.")
        if out:
            print("[INFO] Sample row:", out[0])
        return out if out else _sample(term)
