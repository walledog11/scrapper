# depop_scraper_lib.py — robust selectors + wait strategy + price fallbacks
import os, sys, subprocess, asyncio, random, time, urllib.parse, glob, re
from typing import List, Dict, Optional

PLAYWRIGHT_CACHE = os.path.expanduser("~/.cache/ms-playwright")
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", PLAYWRIGHT_CACHE)

# ----------------- helpers -----------------
def _run(cmd, note=""):
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        out, err = p.communicate()
        rc = p.returncode
        if rc != 0:
            print(f"[WARN] {note} rc={rc}\nstdout:\n{out[:1000]}\nstderr:\n{err[:1000]}")
        else:
            if out:
                print(f"[INFO] {note} stdout:\n{out[:500]}")
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

# ----------------- small text utils -----------------
def _norm_space(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def _clean_title(s: str) -> str:
    s = _norm_space(s)
    s = s.split(" | ", 1)[0]
    s = re.sub(r"\s+by\s+.+$", "", s, flags=re.I)
    return s

def _normalize_price(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    m = re.search(r"([£$€])\s?(\d[\d.,]*)", s)
    if m:
        sym, amt = m.group(1), m.group(2).replace(",", "")
        return f"{sym}{amt}"
    m2 = re.search(r"(USD|GBP|EUR)\s?(\d[\d.,]*)", s, re.I)
    if m2:
        sym = {"USD": "$", "GBP": "£", "EUR": "€"}.get(m2.group(1).upper(), "")
        amt = m2.group(2).replace(",", "")
        return f"{sym}{amt}".strip()
    m3 = re.search(r"(\d[\d.,]*)", s)
    if m3:
        amt = m3.group(1).replace(",", "")
        return f"${amt}"
    return ""

def _is_product_link(href: str) -> bool:
    return bool(href) and "/products/" in href

# ----------------- public API -----------------
def scrape_depop(term: str, deep: bool, limits: dict) -> List[Dict]:
    """
    Sync wrapper. Tries real scrape with Playwright; falls back to sample rows if unavailable.
    """
    try:
        from playwright.async_api import async_playwright  # noqa: F401
    except Exception:
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
    max_items = int(limits.get("MAX_ITEMS", 500))
    max_seconds = int(limits.get("MAX_DURATION_S", 600))
    deep_max = int(limits.get("DEEP_FETCH_MAX", 300))

    chromium_bin = ensure_browser("chromium")
    engine = "chromium" if chromium_bin else "firefox"
    if engine == "firefox" and not ensure_browser("firefox"):
        print("[ERROR] No Playwright browsers available. Returning sample row.")
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

        # Try attached first (less strict), then scroll and retry
        found_any = False
        t0 = time.time()
        while time.time() - t0 < 30:  # up to 30s pre-warm
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
            # Final single visible wait (may still fail on Cloud due to geo/captcha)
            try:
                await page.wait_for_selector(product_selectors[0], state="visible", timeout=15000)
                found_any = True
            except Exception:
                pass

        if not found_any:
            await browser.close()
            raise TimeoutError("No product cards found (selectors did not match).")

        # --- Collect from listing grid with progressive scrolling ---
        rows: List[Dict] = []
        seen = set()
        start = time.time()

        async def extract_from_card(card_handle) -> Optional[Dict]:
            href = await card_handle.get_attribute("href")
            if not _is_product_link(href):
                return None
            # brand / title / price candidates inside card
            price = ""
            brand = ""
            title = ""

            # try some frequent fields inside cards
            text_bits = []
            try:
                txts = await page.evaluate(
                    "el => Array.from(el.querySelectorAll('p,span,div')).map(n => n.textContent || '')", card_handle
                )
                text_bits = [t.strip() for t in txts if t and t.strip()]
            except Exception:
                pass

            # price first
            for t in text_bits:
                m = re.search(r"[£$€]\s?\d[\d.,]*", t)
                if m:
                    price = m.group(0); break

            # title: often first larger text; also inside aria-label on image
            # fallbacks: remove seller via " by " or " | "
            if text_bits:
                # heuristics: prefer a non-price, 2+ words, ≤80 chars
                candidates = [t for t in text_bits if not re.search(r"[£$€]\s?\d", t)]
                if candidates:
                    title = _clean_title(candidates[0])

            # brand is tricky on grid; keep blank if unsure
            return {
                "platform": "Depop",
                "brand": brand,
                "item_name": title or "",
                "price": _normalize_price(price),
                "size": "",
                "condition": "",
                "link": f"https://www.depop.com{href}",
            }

        # Scroll/collect loop
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

                item = await extract_from_card(a)
                if item:
                    rows.append(item)
                if len(rows) >= max_items:
                    break

            if len(rows) >= max_items: break
            if time.time() - start > max_seconds: break

            # scroll and allow lazy load
            await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            await page.wait_for_timeout(random.randint(500, 900))

            # small idle wait every few rounds
            # (kept simple to reduce Cloud flakiness)

        # Optional deep fetch of details (size/condition/price authority)
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

                    # safe wait for any content area
                    try:
                        await dp.wait_for_selector("main, article, [data-testid='product-page']", state="attached", timeout=6000)
                    except Exception:
                        pass

                    DETAIL_JS = """
                    (function() {
                      const pick = (sel) => {
                        const el = document.querySelector(sel);
                        return el ? (el.textContent || "").trim() : "";
                      };
                      // Price: try structured first, then visible labels
                      let price = pick('[data-testid="price"]') ||
                                  pick('[itemprop="price"]') ||
                                  pick('meta[itemprop="price"]') ||
                                  pick('div[aria-label*="Price"]') ||
                                  pick('span[aria-label*="Price"]') ||
                                  pick('p[class*="Price"]') ||
                                  pick('span[class*="Price"]');
                      if (!price) {
                        const m = (document.body.innerText || "").match(/[£$€]\s?\\d+[.,]?\\d*/);
                        if (m) price = m[0];
                      }
                      // Title (clean)
                      let title = pick('h1') || pick('[data-testid="product-title"]') || "";
                      // Size / Condition (common spots)
                      let size = pick('[data-testid="product-size"]') ||
                                 pick('dd:has(+ dt:contains("Size"))') ||
                                 pick('[class*="size" i]');
                      let cond = pick('[data-testid="product-condition"]') ||
                                 pick('dd:has(+ dt:contains("Condition"))') ||
                                 pick('[class*="condition" i]');

                      return {
                        price: price,
                        title: title,
                        size: size,
                        condition: cond
                      };
                    })();
                    """
                    info = await dp.evaluate(DETAIL_JS)
                    # Apply back with normalization
                    if info:
                        r["item_name"] = _clean_title(info.get("title") or r["item_name"])
                        r["price"] = _normalize_price(info.get("price") or r["price"])
                        if info.get("size"): r["size"] = _norm_space(info["size"])
                        if info.get("condition"): r["condition"] = _norm_space(info["condition"])

                except Exception as e:
                    print(f"[WARN] detail fetch failed for {link}: {e}")
                finally:
                    try:
                        await dp.close()
                    except Exception:
                        pass
                detail_count += 1

        await browser.close()

        # Final polish: drop search links (safety), dedupe by link, normalize
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
        return final
