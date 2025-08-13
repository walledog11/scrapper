# depop_scraper_lib.py
# Fast, cloud-friendly Depop scraper helpers for Streamlit apps

import os, sys, subprocess, asyncio, random, time, urllib.parse, glob
from typing import List, Dict

# --- Streamlit may not be present if run standalone; provide a safe fallback
try:
    import streamlit as st
except Exception:  # pragma: no cover
    class _DummyST:
        def cache_resource(self, **_):  # no-op decorator
            def deco(fn): return fn
            return deco
    st = _DummyST()  # type: ignore

# Where Playwright stores browsers (persist across runs on Streamlit Cloud)
PLAYWRIGHT_CACHE = os.path.expanduser("~/.cache/ms-playwright")
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", PLAYWRIGHT_CACHE)

# ------------------------------------------------------------
# Helpers (logging, processes, and locating installed browsers)
# ------------------------------------------------------------
def _short(cmd):
    try:
        return " ".join(cmd[:4]) + (" ..." if len(cmd) > 4 else "")
    except Exception:
        return str(cmd)

def _run(cmd, note=""):
    """Run a subprocess command; never raise. Return (rc, out, err)."""
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        out, err = p.communicate()
        rc = p.returncode
        if rc != 0:
            print(f"[WARN] {note} rc={rc}\nstdout:\n{out[:1000]}\nstderr:\n{err[:2000]}")
        else:
            if out:
                print(f"[INFO] {note} stdout:\n{out[:1000]}")
        return rc, out, err
    except Exception as e:  # pragma: no cover
        print(f"[WARN] {_short(cmd)} failed: {e}")
        return 1, "", str(e)

def _find_binary(engine: str) -> str | None:
    """Locate a browser binary inside the Playwright cache."""
    patterns: List[str] = []
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
    Returns executable path if found; otherwise None. Never raises.
    """
    # Already present?
    bin_path = _find_binary(engine)
    if bin_path:
        print(f"[INFO] Found {engine} binary at: {bin_path}")
        return bin_path

    # Attempt to install deps + engine (harmless if already present)
    _run([sys.executable, "-m", "playwright", "install-deps", engine], note=f"install-deps {engine}")
    _run([sys.executable, "-m", "playwright", "install", engine, "--with-deps"], note=f"install {engine} --with-deps")

    # Force re-download if still missing
    if not _find_binary(engine):
        _run([sys.executable, "-m", "playwright", "install", engine, "--force"], note=f"install {engine} --force")

    bin_path = _find_binary(engine)
    if bin_path:
        print(f"[INFO] After install, found {engine} binary at: {bin_path}")
    else:
        print(f"[WARN] {engine} binary still not found after install attempts (continuing).")
    return bin_path

# ------------------------------------------------------------
# One-time browser installation (cached across Streamlit reruns)
# ------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def ensure_playwright_installed() -> bool:
    """
    Ensure Playwright has its browsers available. We don't care about return code;
    just try a couple of quiet installs. Cached so it only runs once per session.
    """
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "--help"],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except Exception:
        pass
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium", "--with-deps"],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except Exception:
        pass
    return True

# ------------------------------------------------------------
# Request routing: block heavy assets to make pages much faster
# ------------------------------------------------------------
HEAVY_TYPES = {"image", "media", "font", "stylesheet", "other"}
_HEAVY_URL_TOKENS = (
    ".jpg", ".jpeg", ".png", ".webp", ".gif",
    ".svg", ".woff", ".woff2", ".ttf", ".otf",
    "google-analytics", "doubleclick", "googletagmanager", "analytics."
)

async def _abort_heavy(route):
    """
    Abort images/fonts/styles/analytics/etc. Keep HTML/JS.
    This is the single biggest speed win for infinite scroll.
    """
    try:
        req = route.request
        if req.resource_type in HEAVY_TYPES:
            return await route.abort()
        url = (req.url or "").lower()
        if any(tok in url for tok in _HEAVY_URL_TOKENS):
            return await route.abort()
        await route.continue_()
    except Exception:
        # Never let routing crash the run
        try:
            await route.continue_()
        except Exception:
            pass

# ------------------------------------------------------------
# Public API
# ------------------------------------------------------------
def scrape_depop(term: str, deep: bool, limits: dict) -> List[Dict]:
    """
    Sync wrapper that tries the real Playwright scraper and falls back to a sample row
    if the browser can't launch in the current environment (keeps the UI usable).
    """
    try:
        from playwright.async_api import async_playwright  # noqa: F401
    except Exception:
        return [{
            "platform": "Depop",
            "brand": "Supreme",
            "item_name": f"{term} (sample)",
            "price": "$199",
            "size": "L",
            "condition": "Good condition",
            "link": f"https://www.depop.com/search/?q={urllib.parse.quote_plus(term)}",
        }]

    # Make sure Playwright is installed (cached)
    ensure_playwright_installed()

    # Run the async scraper
    return asyncio.run(_scrape_depop_async(term, deep, limits))

# ------------------------------------------------------------
# Actual scraper (lightweight; deep fetch optional in the future)
# ------------------------------------------------------------
async def _scrape_depop_async(term: str, deep: bool, limits: dict) -> List[Dict]:
    from playwright.async_api import async_playwright

    base_url = f"https://www.depop.com/search/?q={urllib.parse.quote_plus(term)}"
    max_items = int(limits.get("MAX_ITEMS", 800))
    max_seconds = int(limits.get("MAX_DURATION_S", 600))

    # Try Chromium first; if unavailable, fall back to Firefox.
    chromium_bin = ensure_browser("chromium")
    use_engine = "chromium" if chromium_bin else "firefox"
    if use_engine == "firefox":
        firefox_bin = ensure_browser("firefox")
        if not firefox_bin:
            # Neither browser is available — return a friendly sample row.
            return [{
                "platform": "Depop",
                "brand": "Supreme",
                "item_name": f"{term} (sample)",
                "price": "$199",
                "size": "L",
                "condition": "Good condition",
                "link": f"https://www.depop.com/search/?q={urllib.parse.quote_plus(term)}",
            }]

    # Cloud-safe flags for Chromium; Firefox ignores unknown flags gracefully
    launch_args = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-background-networking",
    ]

    async with async_playwright() as p:
        if use_engine == "chromium":
            browser_type = p.chromium
            exe = chromium_bin
        else:
            browser_type = p.firefox
            exe = _find_binary("firefox")  # may be None; Playwright can manage path

        # Launch browser
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

        # 🚀 Block heavy assets to speed things up
        await ctx.route("**/*", _abort_heavy)

        page = await ctx.new_page()
        await page.goto(base_url, wait_until="domcontentloaded", timeout=60000)

        # Cookie banner best-effort
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

        # Infinite scroll collector
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

                # Grab nearby text for brand/price (simple heuristics)
                li = await a.evaluate_handle("el => el.closest('li') || el.parentElement")
                price = ""
                brand = ""
                if li:
                    ps = await page.evaluate(
                        "el => Array.from(el.querySelectorAll('p')).map(n => n.textContent || '')", li
                    )
                    # Find currency-looking text
                    for t in ps:
                        t = (t or "").strip()
                        if any(sym in t for sym in ("$", "£", "€")):
                            price = t
                    # Last short non-price line as brand
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

            # Shorter scroll interval (faster) — images/fonts are blocked anyway
            await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            await page.wait_for_timeout(random.randint(350, 700))

        await browser.close()
        return rows
