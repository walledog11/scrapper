# depop_scraper_lib.py
import os, sys, subprocess, asyncio, random, time, urllib.parse, glob, shutil
from typing import List, Dict

PLAYWRIGHT_CACHE = os.path.expanduser("~/.cache/ms-playwright")

def _run(cmd: list[str], strict: bool, env: dict | None = None) -> tuple[int, str, str]:
    p = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env
    )
    out, err = p.communicate()
    if strict and p.returncode != 0:
        raise RuntimeError(f"Command failed {cmd}\nstdout:\n{out}\nstderr:\n{err}")
    return p.returncode, out, err

def _find_chromium_binary() -> str | None:
    # Prefer headless_shell (Playwright 1.54+), fallback to chrome
    patterns = [
        os.path.join(PLAYWRIGHT_CACHE, "chromium_headless_shell-*", "chrome-linux", "headless_shell"),
        os.path.join(PLAYWRIGHT_CACHE, "chromium-*", "chrome-linux", "chrome"),
    ]
    for pat in patterns:
        matches = sorted(glob.glob(pat))
        for m in matches:
            if os.path.isfile(m) and os.access(m, os.X_OK):
                return m
    return None

def ensure_playwright_chromium(verbose: bool = True) -> str:
    """
    Ensures Playwright's Chromium (headless_shell) is installed and returns the binary path.
    Raises with a detailed message if we still can't find it.
    """
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", PLAYWRIGHT_CACHE)

    # 0) Quick happy path
    bin_path = _find_chromium_binary()
    if bin_path:
        return bin_path

    # 1) Try install with deps (quiet first to be fast)
    rc, out, err = _run(
        [sys.executable, "-m", "playwright", "install", "chromium", "--with-deps"],
        strict=False
    )
    if verbose and rc != 0:
        print("[install attempt 1] non-zero exit:", err or out)

    bin_path = _find_chromium_binary()
    if bin_path:
        return bin_path

    # 2) Retry install loudly (capture logs for debugging)
    rc, out, err = _run(
        [sys.executable, "-m", "playwright", "install", "chromium", "--with-deps", "--force"],
        strict=False
    )
    if verbose:
        print("[install attempt 2] rc=", rc)
        if out: print("[playwright stdout]\n", out[:4000])
        if err: print("[playwright stderr]\n", err[:4000])

    bin_path = _find_chromium_binary()
    if bin_path:
        return bin_path

    # 3) One more fallback: try non-with-deps (sometimes faster on Cloud)
    rc, out, err = _run(
        [sys.executable, "-m", "playwright", "install", "chromium", "--force"],
        strict=False
    )
    if verbose:
        print("[install attempt 3] rc=", rc)
        if out: print("[playwright stdout]\n", out[:4000])
        if err: print("[playwright stderr]\n", err[:4000])

    bin_path = _find_chromium_binary()
    if bin_path:
        return bin_path

    raise RuntimeError(
        "Playwright Chromium not found after install attempts. "
        "Please rerun and check logs; the runtime may have blocked the download."
    )

# --------- Public API ---------
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

# --------- Real scraper (lightweight) ---------
async def _scrape_depop_async(term: str, deep: bool, limits: dict) -> List[Dict]:
    from playwright.async_api import async_playwright

    # Ensure Chromium is present and get its path
    chromium_bin = ensure_playwright_chromium(verbose=True)

    base_url = f"https://www.depop.com/search/?q={urllib.parse.quote_plus(term)}"
    max_items = int(limits.get("MAX_ITEMS", 500))
    max_seconds = int(limits.get("MAX_DURATION_S", 600))

    # Cloud-safe flags
    launch_args = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-background-networking",
    ]

    async with async_playwright() as p:
        # If Playwright still complains about missing executable, explicitly pass the path we found.
        browser = await p.chromium.launch(
            headless=True,
            args=launch_args,
            executable_path=chromium_bin  # <— key difference
        )
        ctx = await browser.new_context(
            user_agent=("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
        )
        page = await ctx.new_page()
        await page.goto(base_url, wait_until="domcontentloaded", timeout=60000)

        # Best-effort: accept cookies
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
