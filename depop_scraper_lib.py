# depop_scraper_lib.py — robust selectors + wait strategy + fallbacks + logging
import os, sys, subprocess, asyncio, random, time, urllib.parse, glob, re
from typing import List, Dict, Optional

PLAYWRIGHT_CACHE = os.path.expanduser("~/.cache/ms-playwright")
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", PLAYWRIGHT_CACHE)

# ----------------- helpers -----------------
def _run(cmd, note=""):
    try:
        import subprocess as sp
        p = sp.Popen(cmd, stdout=sp.PIPE, stderr=sp.PIPE, text=True)
        out, err = p.communicate()
        rc = p.returncode
        if rc != 0:
            print(f"[WARN] {note} rc={rc}\nstdout:\n{out[:800]}\nstderr:\n{err[:800]}")
        else:
            if out:
                print(f"[INFO] {note} stdout:\n{out[:400]}")
        return rc, out, err
    except Exception as e:
        print(f"[WARN] {' '.join(cmd[:4])} ... failed: {e}")
        return 1, "", str(e)

def _find_binary(engine: str) -> Optional[str]:
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
    bin_path = _find_binary(engine)
    if bin_path:
        print(f"[INFO] Found {engine} at: {bin_path}")
        return bin_path
    _run([sys.executable, "-m", "playwright", "install-deps", engine], note=f"install-deps {engine}")
    _run([sys.executable, "-m", "playwright", "install", engine, "--with-deps"], note=f"install {engine} --with-deps")
    if not _find_binary(engine):
        _run([sys.executable, "-m", "playwright", "install", engine, "--force"], note=f"install {engine} --force")
    return _find_binary(engine)

def _norm_space(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def _clean_title(s: str) -> str:
    s = _norm_space(s)
    # drop " | seller" OR " by seller"
    s = s.split(" | ", 1)[0]
    s = re.sub(r"\s+by\s+.+$", "", s, flags=re.I)
    return s

def _normalize_price(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    # currency symbol + amount
    m = re.search(r"([£$€])\s?(\d[\d.,]*)", s)
    if m:
        sym, amt = m.group(1), m.group(2).replace(",", "")
        return f"{sym}{amt}"
    # ISO currency as fallback
    m2 = re.search(r"(USD|GBP|EUR)\s?(\d[\d.,]*)", s, re.I)
    if m2:
        sym = {"USD":"$", "GBP":"£", "EUR":"€"}.get(m2.group(1).upper(), "")
        amt = m2.group(2).replace(",", "")
        return f"{sym}{amt}".strip()
    # any number => assume $
    m3 = re.search(r"(\d[\d.,]*)", s)
    if m3:
        amt = m3.group(1).replace(",", "")
        return f"${amt}"
    return ""

def _is_product_link(href: str) -> bool:
    return bool(href) and "/products/" in href

# ----------------- public API -----------------
def scrape_depop(term: str, deep: bool, limits: dict) -> List[Dict]:
    try:
        from playwright.async_api import async_playwright  # noqa: F401
    except Exception:
        # fallback sample so app UI keeps working
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

# ----------------- real scraper -----------------
async def _scrape_depop_async(term: str, deep: bool, limits: dict) -> List[Dict]:
    from playwright.async_api import async_playwright

    base_url = (
        "https://www.depop.com/search/"
        f"?q={urllib.parse.quote_plus(term)}"
        "&sort=relevance&country=us"
    )
    max_items    = int(limits.get("MAX_ITEMS", 500))
    max_seconds  = int(limits.get("MAX_DURATION_S", 600))
    deep_max     = int(limits.get("DEEP_FETCH_MAX", 300))

    chromium_bin = ensure_browser("chromium")
    engine = "chromium" if chromium_bin else "firefox"
    if engine == "firefox" and not ensure_browser("firefox"):
        print("[ERROR] No browsers available; returning sample row.")
        return [{
            "platform": "Depop",
            "brand": "Sample",
            "item_name": f"{term} (sample)",
            "price": "$199",
            "size": "L",
            "condition": "Good condition",
            "link": f"https://www.depop.com/search/?q={urllib.parse.quote_plus(term)}",
        }]

    launch_args = ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage",
                   "--disable-gpu", "--disable-background-networking"]

    async with async_playwright() as p:
        browser_type = p.chromium if engine == "chromium" else p.firefox
        exe = chromium_bin if engine == "chromium" else _find_binary("firefox")

        try:
            if exe:
                browser = await browser_type.launch(headless=True, args=launch_args, executable_path=exe)
            else:
                browser = await browser_type.launch(headless=True, args=launch_args)
        except Exception as e:
            print(f"[ERROR] Launch failed: {e}")
            return [{
                "platform": "Depop",
                "brand": "Sample",
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

        # Cookie banner best-effort
        try:
            for sel in ["button:has-text('Accept')", "button:has-text('Accept all')",
                        "[data-testid='cookie-accept']", "text=Accept cookies"]:
                btn = await page.query_selector(sel)
                if btn:
                    await btn.click()
                    break
        except Exception:
            pass

        # --- Robust wait for any product card anchor ---
        product_selectors = [
            "a[href^='/products/']",
            "a[data-testid='product-card']",
            "[data-testid='productCard'] a",
            "article a[href*='/products/']",
        ]

        # pre-warm loop (attached, scroll/retry)
        found_any = False
        t0 = time.time()
        while time.time() - t0 < 30:
            for sel in product_selectors:
                try:
                    await page.wait_for_selector(sel, state="attached", timeout=1500)
                    found_any = True
                    break
                except Exception:
                    continue
            if found_any:
                break
            await page.evaluate("window.scrollBy(0, 800)")
            await page.wait_for_timeout(600)

        if not found_any:
            # one last visible attempt
            try:
                await page.wait_for_selector(product_selectors[0], state="visible", timeout=15000)
                found_any = True
            except Exception:
                pass

        if not found_any:
            await browser.close()
            raise TimeoutError("No product cards found (selectors did not match).")

        # -------- grid extraction helpers ----------
        async def _card_texts(card) -> List[str]:
            try:
                # collect visible-ish text from common nodes
                txts = await page.evaluate(
                    """(el) => Array.from(el.querySelectorAll('img[alt], p, span, div'))
                           .map(n => (n.alt || n.textContent || '').trim())
                           .filter(Boolean)""",
                    card
                )
                return [t for t in txts if t]
            except Exception:
                return []

        async def _extract_from_card(card) -> Optional[Dict]:
            href = await card.get_attribute("href")
            if not _is_product_link(href):
                return None

            text_bits = await _card_texts(card)
            price = ""
            title = ""
            brand = ""

            # price: first currency-like on card
            for t in text_bits:
                m = re.search(r"[£$€]\s?\d[\d.,]*", t)
                if m:
                    price = m.group(0)
                    break

            # title candidates: image alt, aria-labels, first non-price text
            # try image alt explicitly
            if not title:
                try:
                    img = await card.query_selector("img[alt]")
                    if img:
                        alt = (await img.get_attribute("alt")) or ""
                        if alt.strip():
                            title = alt.strip()
                except Exception:
                    pass

            if not title:
                non_price = [t for t in text_bits if not re.search(r"[£$€]\s?\d", t)]
                if non_price:
                    title = non_price[0]

            title = _clean_title(title)
            price = _normalize_price(price)

            return {
                "platform": "Depop",
                "brand": brand,          # brand not reliable on grid; fill via PDP when deep
                "item_name": title or "",
                "price": price,
                "size": "",
                "condition": "",
                "link": f"https://www.depop.com{href}",
            }

        # -------- scroll & collect from grid ----------
        rows: List[Dict] = []
        seen = set()
        start = time.time()

        while True:
            anchors = []
            for sel in product_selectors:
                try:
                    anchors = await page.query_selector_all(sel)
                    if anchors:
                        break
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
                if len(rows) >= max_items:
                    break

            if len(rows) >= max_items:
                break
            if time.time() - start > max_seconds:
                break

            # scroll for more
            await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            await page.wait_for_timeout(random.randint(500, 900))

        # -------- PDP deep fetch (fills price/size/condition/title/brand) ----------
        if deep and rows:
            detail_count = 0
            for r in rows:
                if detail_count >= deep_max:
                    break
                link = r.get("link")
                if not _is_product_link(link):
                    continue
                try:
                    dp = await ctx.new_page()
                    await dp.goto(link, wait_until="domcontentloaded", timeout=60000)

                    # safe base wait
                    try:
                        await dp.wait_for_selector("main, article, [data-testid='product-page']", state="attached", timeout=7000)
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

                      // Price: prefer structured content attribute if present
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

                      // Title
                      let title = pick('h1') || pick('[data-testid="product-title"]') || "";

                      // Brand: sometimes near seller block; keep light
                      let brand = pick('[data-testid="brand"]') || "";

                      // Size / Condition (common definitions lists)
                      let size = "";
                      let condition = "";

                      const dts = Array.from(document.querySelectorAll('dt'));
                      for (const dt of dts) {
                        const key = (dt.textContent || "").toLowerCase().trim();
                        const dd = dt.nextElementSibling;
                        const val = dd ? (dd.textContent || "").trim() : "";
                        if (!val) continue;
                        if (!size && key.includes("size")) size = val;
                        if (!condition && (key.includes("condition") || key.includes("state"))) condition = val;
                      }

                      // also try common data-testid shortcuts
                      size = size || pick('[data-testid="product-size"]') || "";
                      condition = condition || pick('[data-testid="product-condition"]') || "";

                      return { price, title, brand, size, condition };
                    })();
                    """

                    info = await dp.evaluate(DETAIL_JS)

                    # Apply with normalization/fallbacks
                    if info:
                        if info.get("title"):
                            r["item_name"] = _clean_title(info["title"])
                        if info.get("brand"):
                            r["brand"] = _norm_space(info["brand"])
                        # prefer PDP price if present
                        pdp_price = _normalize_price(info.get("price", ""))
                        if pdp_price:
                            r["price"] = pdp_price
                        if info.get("size"):
                            r["size"] = _norm_space(info["size"])
                        if info.get("condition"):
                            r["condition"] = _norm_space(info["condition"])

                    # Safety: if still no price, try visible strong currency in snippet again
                    if not r.get("price"):
                        try:
                            txt = await dp.inner_text("body", timeout=3000)
                            m = re.search(r"[£$€]\s?\d[\d.,]*", txt)
                            if m:
                                r["price"] = _normalize_price(m.group(0))
                        except Exception:
                            pass

                except Exception as e:
                    print(f"[WARN] PDP fetch failed for {link}: {e}")
                finally:
                    try:
                        await dp.close()
                    except Exception:
                        pass
                detail_count += 1

        await browser.close()

        # -------- final polish: dedupe + normalize ----------
        final: List[Dict] = []
        seen_links = set()
        for r in rows:
            lk = r.get("link", "")
            if not _is_product_link(lk) or lk in seen_links:
                continue
            seen_links.add(lk)
            final.append({
                "platform": "Depop",
                "brand": _norm_space(r.get("brand", "")),
                "item_name": _clean_title(r.get("item_name", "")),
                "price": _normalize_price(r.get("price", "")),
                "size": _norm_space(r.get("size", "")),
                "condition": _norm_space(r.get("condition", "")),
                "link": lk,
            })
        print(f"[INFO] Collected {len(final)} items.")
        # Log a sample row to help debug visibility
        if final:
            print("[INFO] Sample row:", final[0])
        return final
