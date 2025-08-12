# depop_scraper_lib.py
import os, sys, subprocess, asyncio, random, time, urllib.parse, glob
from typing import List, Dict

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

def _find_binary(engine: str) -> str | None:
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

def ensure_browser(engine: str) -> str | None:
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

    # 1) Try install-deps (no-op on Streamlit if packages.txt already handled it)
    _run([sys.executable, "-m", "playwright", "install-deps", engine], note=f"install-deps {engine}")

    # 2) Try regular install with deps
    _run([sys.executable, "-m", "playwright", "install", engine, "--with-deps"], note=f"install {engine} --with-deps")

    # 3) Force re-download (sometimes caches are weird)
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
            # If we have neither, return a friendly sample row instead of crashing
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
            # Final fallback: return sample row rather than crash the UI
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
                if brand and slug.lower().startswith(brand.lower()):
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

        await browser.close()
        return rows
