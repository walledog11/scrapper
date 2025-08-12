# depop_scraper.py
# Streamlit + Playwright scraper for Depop with Google Sheets output
# Paste this entire file to avoid indentation / missing-import issues.

import os, sys, json, csv, io, time, random, subprocess, urllib.parse, asyncio
from typing import List, Dict

import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
from playwright.async_api import async_playwright

# ---------- UI text ----------
INSTALL_TEXT = """
**Google credentials**
Use one of:
1) Add a `[google_service_account]` table in Streamlit Secrets (recommended), or
2) Set `GOOGLE_SERVICE_ACCOUNT` as a JSON string in Secrets, or
3) Place a local `credentials.json` in this folder (for local dev).

**Playwright (local)**

"""

# ---------- Streamlit page ----------
st.set_page_config(page_title="Depop Scraper", page_icon="🧢", layout="wide")
st.title("🧢 Depop Scraper")

with st.expander("First time? Setup help", expanded=False):
    st.markdown(INSTALL_TEXT)

IS_CLOUD = bool(os.environ.get("STREAMLIT_RUNTIME"))
DEFAULT_HEADLESS = True if IS_CLOUD else False

# ---------- Ensure Playwright Chromium (safe on Cloud) ----------
@st.cache_resource(show_spinner=False)
def _ensure_playwright():
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium", "--with-deps"],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except Exception:
        pass
    return True

_ensure_playwright()

# ---------- Sidebar controls ----------
with st.sidebar:
    st.header("Settings")
    SHEET_NAME = st.text_input("Google Sheet name", value="depop_scraper",
                               help="Spreadsheet (doc) name, not the tab.")
    HEADLESS = st.toggle("Run headless (recommended on Cloud)", value=DEFAULT_HEADLESS)
    DEEP_FETCH = st.toggle("Deep fetch product pages for Size/Condition", value=True)
    RESET_SHEET = st.toggle("Reset sheet tab (clear & rewrite headers)", value=False)

    st.subheader("Limits")
    MAX_ITEMS = st.number_input("Max items (safety cap)", min_value=100, max_value=20000, value=8000, step=100)
    MAX_DURATION_S = st.number_input("Max duration (seconds)", min_value=60, max_value=3600, value=900, step=30)
    DEEP_FETCH_MAX = st.number_input("Max deep-fetched items", min_value=50, max_value=5000, value=1200, step=50)
    DEEP_FETCH_CONCURRENCY = st.slider("Deep fetch concurrency", 1, 6, 3)
    PAUSE_MIN, PAUSE_MAX = st.slider("Jitter between scrolls (ms)", 200, 1500, (500, 900))

    st.subheader("Advanced scroll knobs")
    MAX_ROUNDS = st.number_input("Max scroll rounds", min_value=10, max_value=2000, value=400, step=10)
    WARMUP_ROUNDS = st.number_input("Warmup rounds", min_value=0, max_value=100, value=6, step=1)
    IDLE_ROUNDS = st.number_input("Stop if no growth for N rounds", min_value=2, max_value=30, value=6, step=1)
    NETWORK_IDLE_EVERY = st.number_input("Wait for network-idle every N rounds", min_value=5, max_value=60, value=12, step=1)
    NETWORK_IDLE_TIMEOUT = st.number_input("Network-idle timeout (ms)", min_value=1000, max_value=20000, value=5000, step=500)
    DEEP_FETCH_DELAY_MIN, DEEP_FETCH_DELAY_MAX = st.slider("Per detail page delay (ms)", 200, 4000, (800, 1600))

# ---------- Google credentials ----------
SHEETS_SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
SHEET_HEADERS = ["Platform","Brand","Item Name","Price","Size","Condition","Link"]

from creds_loader import authorize_gspread  # NEW

# Replace your open_worksheet() to use the shared client:
def open_worksheet(doc_name: str, title: str, force_reset: bool = False):
    import gspread
    SHEET_HEADERS = ["Platform","Brand","Item Name","Price","Size","Condition","Link"]

    client = authorize_gspread()  # << use shared
    try:
        doc = client.open(doc_name)
    except gspread.SpreadsheetNotFound:
        doc = client.create(doc_name)

    try:
        ws = doc.worksheet(title[:99])
    except gspread.WorksheetNotFound:
        ws = doc.add_worksheet(title=title[:99], rows=5000, cols=len(SHEET_HEADERS))
        force_reset = True

    vals = ws.get_all_values()
    if force_reset or not vals or vals[0] != SHEET_HEADERS:
        ws.clear()
        ws.append_row(SHEET_HEADERS)
    return ws


def open_worksheet(doc_name: str, title: str, force_reset: bool = False):
    creds = load_google_credentials()
    client = gspread.authorize(creds)
    # Open or create spreadsheet
    try:
        doc = client.open(doc_name)
    except gspread.SpreadsheetNotFound:
        doc = client.create(doc_name)
    # Open or create worksheet
    try:
        ws = doc.worksheet(title[:99])
    except gspread.WorksheetNotFound:
        ws = doc.add_worksheet(title=title[:99], rows=5000, cols=len(SHEET_HEADERS))
        force_reset = True
    # Ensure headers
    vals = ws.get_all_values()
    if force_reset or not vals or vals[0] != SHEET_HEADERS:
        ws.clear()
        ws.append_row(SHEET_HEADERS)
    return ws

def save_to_google_sheets(ws, rows: List[Dict]):
    payload = [[
        r.get("platform","Depop"),
        r.get("brand",""),
        r.get("item_name",""),
        r.get("price",""),
        r.get("size",""),
        r.get("condition",""),
        r.get("link",""),
    ] for r in rows]
    if payload:
        ws.append_rows(payload, value_input_option="RAW")

# ---------- Scrape helpers ----------
def build_search_url(term: str) -> str:
    return f"https://www.depop.com/search/?q={urllib.parse.quote_plus(term)}"

AUTOCOLLECT_SCRIPT = """
(() => {
  if (!window.__depopSeen) window.__depopSeen = new Set();
  const anchors = Array.from(document.querySelectorAll('a[href^="/products/"]'));
  let added = 0;
  for (const a of anchors) {
    const href = a.getAttribute('href');
    if (!href) continue;
    if (!window.__depopSeen.has(href)) {
      window.__depopSeen.add(href);
      added++;
    }
  }
  return { total: window.__depopSeen.size, added };
})()
"""

EXTRACT_LIST_SCRIPT = r"""
(() => {
  const currencyRe = /[$£€]\s?\d|\d+(?:[.,]\d{2})/;
  const out = [];
  const clean = s => (s || "").trim();
  const seen = window.__depopSeen ? Array.from(window.__depopSeen) : [];

  for (const href of seen) {
    const a = document.querySelector(`a[href="${href}"]`) || document.querySelector(`a[href^="${href}"]`);
    const li = a ? (a.closest('li') || a.parentElement) : null;

    let price = "N/A", brand = "";

    if (li) {
      const pTags = Array.from(li.querySelectorAll('p'));
      const priceTag = pTags.find(p => currencyRe.test(p.textContent || ""));
      if (priceTag) price = clean(priceTag.textContent);

      const texts = pTags.map(p => clean(p.textContent)).filter(Boolean);
      for (let i = texts.length - 1; i >= 0; i--) {
        const t = texts[i];
        if (!currencyRe.test(t) && t.length <= 40) { brand = t; break; }
      }
    }

    const slug = href.replace(/\/$/, '').split('/').pop().replace(/-/g, ' ');
    let itemName = slug;
    if (brand && slug.toLowerCase().startsWith(brand.toLowerCase())) {
      itemName = clean(slug.slice(brand.length));
    }

    out.push({
      platform: "Depop",
      brand: brand || "",
      item_name: itemName || "",
      price: price,
      size: "",
      condition: "",
      link: "https://www.depop.com" + href
    });
  }
  return out;
})()
"""

DETAIL_EXTRACT_JS = r"""
(() => {
  const clean = s => (s || "").replace(/\s+/g,' ').trim();
  const getText = sel => {
    const el = document.querySelector(sel);
    return el ? clean(el.textContent) : "";
  };

  const data = {};
  data.title = getText('h1') || getText('[data-testid="listing-title"]') || getText('[itemprop="name"]');
  data.price = getText('[data-testid="price"]')
            || getText('div[aria-label*="Price"]')
            || getText('span[aria-label*="Price"]')
            || getText('[itemprop="price"]')
            || "";

  function getSizeDOM() {
    let v = getText('[data-testid="size"]'); if (v) return v;
    const chipSel = [
      'button[aria-pressed="true"]','button[aria-selected="true"]',
      '[class*="chip"][aria-pressed="true"]','[class*="chip"][aria-selected="true"]',
      '[data-testid*="size"][aria-pressed="true"]','[data-testid*="size"][aria-selected="true"]'
    ];
    for (const s of chipSel) {
      const el = document.querySelector(s);
      if (el) {
        const txt = clean(el.textContent);
        if (txt && txt.length <= 16) return txt;
      }
    }
    const dts = Array.from(document.querySelectorAll('dt, .dt, [role="term"]'));
    for (const dt of dts) {
      const t = clean(dt.textContent).toLowerCase();
      if (t.startsWith('size')) {
        const dd = dt.nextElementSibling;
        if (dd) { const txt = clean(dd.textContent); if (txt) return txt; }
      }
    }
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
    while (walker.nextNode()) {
      const el = walker.currentNode; const txt = clean(el.textContent).toLowerCase();
      if (txt === 'size' || txt.startsWith('size:')) {
        const sib = el.nextElementSibling && clean(el.nextElementSibling.textContent);
        if (sib && sib.toLowerCase() !== 'size') return sib;
      }
    }
    return "";
  }

  function getConditionDOM() {
    let v = getText('[data-testid="condition"]'); if (v) return v;
    const dts = Array.from(document.querySelectorAll('dt, .dt, [role="term"]'));
    for (const dt of dts) {
      const t = clean(dt.textContent).toLowerCase();
      if (t.startsWith('condition')) {
        const dd = dt.nextElementSibling;
        if (dd) { const txt = clean(dd.textContent); if (txt) return txt; }
      }
    }
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
    while (walker.nextNode()) {
      const el = walker.currentNode; const txt = clean(el.textContent).toLowerCase();
      if (txt === 'condition' || txt.startsWith('condition:')) {
        const sib = el.nextElementSibling && clean(el.nextElementSibling.textContent);
        if (sib && !/^condition:?$/i.test(sib)) return sib;
      }
    }
    return "";
  }

  let size = getSizeDOM();
  let condition = getConditionDOM();

  function parseJSONSafe(text) { try { return JSON.parse(text); } catch { return null; } }
  function tryNextData() {
    const s = document.querySelector('#__NEXT_DATA__');
    return s && s.textContent ? parseJSONSafe(s.textContent) : null;
  }
  function tryLdJson() {
    return Array.from(document.querySelectorAll('script[type="application/ld+json"]'))
      .map(n => n.textContent ? parseJSONSafe(n.textContent) : null)
      .filter(Boolean);
  }
  function findFirstStringByKeys(obj, keys) {
    const seen = new Set(), stack = [obj];
    while (stack.length) {
      const cur = stack.pop();
      if (!cur || typeof cur !== 'object') continue;
      if (seen.has(cur)) continue;
      seen.add(cur);
      for (const k of Object.keys(cur)) {
        const v = cur[k]; const lk = k.toLowerCase();
        if (keys.includes(lk)) {
          if (typeof v === 'string' && v.trim()) return v.trim();
          if (typeof v === 'number') return String(v);
          if (v && typeof v === 'object') {
            const cand = v.name || v.value || v.label || v.text || v['@id'];
            if (typeof cand === 'string' && v && String(cand).trim()) return String(cand).trim();
          }
        }
        if (v && typeof v === 'object') stack.push(v);
      }
      if (Array.isArray(cur)) for (const it of cur) if (it && typeof it === 'object') stack.push(it);
    }
    return "";
  }

  function prettySchemaCondition(val) {
    if (!val) return "";
    const s = String(val).trim();
    const slug = s.startsWith('http') ? s.split('/').pop() : s;
    const map = {
      'NewCondition': 'Brand New',
      'UsedCondition': 'Used',
      'RefurbishedCondition': 'Refurbished',
      'DamagedCondition': 'Damaged',
    };
    return map[slug] || s;
  }

  if (!condition) {
    const meta = document.querySelector('[itemprop="itemCondition"][content]');
    if (meta && meta.getAttribute('content')) {
      condition = prettySchemaCondition(meta.getAttribute('content'));
    }
  }

  const nextData = tryNextData();
  if (!size && nextData) {
    size = findFirstStringByKeys(nextData, ["size","selectedsize","variant","itemsize","productsize","sizelabel"]);
  }
  if (!condition && nextData) {
    const raw = findFirstStringByKeys(nextData, ["condition","itemcondition","productcondition","conditionlabel","conditiontext","itemCondition"]);
    if (raw) condition = prettySchemaCondition(raw);
  }

  if (!condition) {
    const blocks = tryLdJson();
    for (const b of blocks) {
      const raw = findFirstStringByKeys(b, ["condition","itemcondition","productcondition","itemCondition"]);
      if (raw) { condition = prettySchemaCondition(raw); break; }
    }
  }
  if (!size) {
    const blocks = tryLdJson();
    for (const b of blocks) {
      const s = findFirstStringByKeys(b, ["size","itemsize","sizelabel"]);
      if (s) { size = s; break; }
    }
  }

  const bodyText = document.body.innerText;
  if (!size) {
    const m = bodyText.match(/\b(?:size|sz)\s*[:\-]?\s*([A-Za-z0-9./\- ]{1,12})/i);
    if (m && m[1]) size = m[1].trim();
  }
  const mCond = bodyText.match(/\b(brand\s*new|new with tags|new without tags|excellent|very good|good|fair|poor)\s+condition\b/i);
  if (mCond && mCond[0]) {
    const granular = mCond[0].trim();
    if (!condition || /^used$/i.test(condition)) condition = granular;
  }

  return {
    title: data.title,
    price: data.price,
    size: clean(size),
    condition: clean(condition),
  };
})()
"""

async def try_load_cookies(context):
    path = "cookies.json"
    if not os.path.exists(path):
        return
    try:
        with open(path, "r") as f:
            data = json.load(f)
        cookies = data.get("cookies", data)
        await context.add_cookies([
            {
                "name": c.get("name"),
                "value": c.get("value"),
                "domain": c.get("domain") or ".depop.com",
                "path": c.get("path", "/"),
                **({"expires": c["expires"]} if c.get("expires") else {}),
                **({"secure": c["secure"]} if "secure" in c else {}),
                **({"httpOnly": c["httpOnly"]} if "httpOnly" in c else {}),
            }
            for c in cookies if c.get("name") and c.get("value")
        ])
    except Exception:
        pass

async def dismiss_cookie_banner(page):
    for sel in [
        "button:has-text('Accept all')","button:has-text('Accept')",
        "button:has-text('I Agree')","button:has-text('Got it')",
        "[data-testid='cookie-accept']","text=Accept cookies",
    ]:
        try:
            btn = await page.query_selector(sel)
            if btn:
                await btn.click()
                return
        except:
            pass
    try:
        await page.keyboard.press("Escape")
    except:
        pass

async def infinite_collect(page, max_rounds, warmup, idle_rounds, pause_min, pause_max,
                           net_idle_every, net_idle_timeout, max_items, max_duration_s, log_cb):
    last_total = 0
    stable = 0
    start = time.time()
    for i in range(1, int(max_rounds) + 1):
        counts = await page.evaluate(AUTOCOLLECT_SCRIPT)
        total, added = counts["total"], counts["added"]
        log_cb(f"… round {i}: total {total} (+{added})")

        if total >= max_items:
            log_cb("Reached MAX_ITEMS cap.")
            break

        if i > warmup:
            stable = stable + 1 if total == last_total else 0
        last_total = total

        if i > warmup and stable >= idle_rounds:
            log_cb("Count stabilized; stopping scroll.")
            break

        await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
        await page.wait_for_timeout(random.randint(pause_min, pause_max))

        if i % net_idle_every == 0:
            try:
                await page.wait_for_load_state("networkidle", timeout=net_idle_timeout)
            except:
                pass

        if time.time() - start > max_duration_s:
            log_cb("Hit MAX_DURATION_S; stopping.")
            break

async def deep_fetch_worker(context, links: List[str], base_rows_by_link: Dict[str, Dict],
                            results_out: List[Dict], sem: asyncio.Semaphore,
                            delay_min_ms: int, delay_max_ms: int, log_cb):
    page = await context.new_page()
    try:
        for link in links:
            async with sem:
                try:
                    await page.goto(link, wait_until="domcontentloaded", timeout=60000)
                    try:
                        await page.wait_for_selector('#__NEXT_DATA__', timeout=4000)
                    except Exception:
                        pass
                    await page.evaluate("window.scrollBy(0, document.body.scrollHeight * 0.2)")
                    await page.wait_for_timeout(random.randint(delay_min_ms, delay_max_ms))
                    details = await page.evaluate(DETAIL_EXTRACT_JS)
                except Exception as e:
                    details = {}
                    log_cb(f"Detail error: {link} -> {e}")
                base = base_rows_by_link.get(link, {
                    "platform":"Depop","brand":"","item_name":"","price":"",
                    "size":"","condition":"","link":link
                })
                out = {
                    "platform": "Depop",
                    "brand": base.get("brand",""),
                    "item_name": details.get("title") or base.get("item_name",""),
                    "price": details.get("price") or base.get("price",""),
                    "size": details.get("size") or base.get("size",""),
                    "condition": details.get("condition") or base.get("condition",""),
                    "link": link,
                }
                results_out.append(out)
    finally:
        await page.close()

async def scrape_depop(term: str, headless: bool, deep: bool, limits: dict, log_cb):
    base_url = build_search_url(term)
    all_rows: List[Dict] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
        )
        await try_load_cookies(context)
        page = await context.new_page()
        page.set_default_navigation_timeout(60000)

        log_cb(f"Opening: {base_url}")
        await page.goto(base_url, wait_until="domcontentloaded")
        await dismiss_cookie_banner(page)
        try:
            await page.wait_for_selector("a[href^='/products/']", state="attached", timeout=10000)
        except:
            pass

        await infinite_collect(
            page,
            limits["MAX_ROUNDS"], limits["WARMUP_ROUNDS"], limits["IDLE_ROUNDS"],
            limits["PAUSE_MIN"], limits["PAUSE_MAX"],
            limits["NETWORK_IDLE_EVERY"], limits["NETWORK_IDLE_TIMEOUT"],
            limits["MAX_ITEMS"], limits["MAX_DURATION_S"],
            log_cb
        )

        list_rows: List[Dict] = await page.evaluate(EXTRACT_LIST_SCRIPT)
        log_cb(f"List extracted: {len(list_rows)} items")

        if deep and list_rows:
            by_link = {}
            links = []
            for r in list_rows:
                if r["link"] not in by_link:
                    by_link[r["link"]] = r
                    links.append(r["link"])
            links = links[:limits["DEEP_FETCH_MAX"]]
            log_cb(f"Deep fetching {len(links)} items…")

            sem = asyncio.Semaphore(limits["DEEP_FETCH_CONCURRENCY"])
            results_out: List[Dict] = []
            batches = [links[i::limits["DEEP_FETCH_CONCURRENCY"]] for i in range(limits["DEEP_FETCH_CONCURRENCY"])]
            tasks = [
                deep_fetch_worker(context, batch, by_link, results_out, sem,
                                  limits["DEEP_FETCH_DELAY_MIN"], limits["DEEP_FETCH_DELAY_MAX"], log_cb)
                for batch in batches if batch
            ]
            await asyncio.gather(*tasks, return_exceptions=True)
            all_rows = results_out
        else:
            all_rows = list_rows

        await browser.close()
    return all_rows

# ---------- UI ----------
st.subheader("Run a scrape")
query = st.text_input("Search term", value="Supreme Box Logo",
                      help="e.g. 'palace hoodie', 'arcteryx alpha', etc.")
run_btn = st.button("Run Scrape", type="primary")

log_area = st.empty()
def log_cb(msg: str):
    log_area.write(msg)

if run_btn:
    if not query.strip():
        st.warning("Please enter a search term.")
        st.stop()

    limits = dict(
        MAX_ROUNDS=int(MAX_ROUNDS),
        WARMUP_ROUNDS=int(WARMUP_ROUNDS),
        IDLE_ROUNDS=int(IDLE_ROUNDS),
        PAUSE_MIN=int(PAUSE_MIN),
        PAUSE_MAX=int(PAUSE_MAX),
        NETWORK_IDLE_EVERY=int(NETWORK_IDLE_EVERY),
        NETWORK_IDLE_TIMEOUT=int(NETWORK_IDLE_TIMEOUT),
        MAX_ITEMS=int(MAX_ITEMS),
        MAX_DURATION_S=int(MAX_DURATION_S),
        DEEP_FETCH_MAX=int(DEEP_FETCH_MAX),
        DEEP_FETCH_CONCURRENCY=int(DEEP_FETCH_CONCURRENCY),
        DEEP_FETCH_DELAY_MIN=int(DEEP_FETCH_DELAY_MIN),
        DEEP_FETCH_DELAY_MAX=int(DEEP_FETCH_DELAY_MAX),
    )

    with st.status("Scraping…", expanded=True) as status:
        st.write("Starting browser and loading results…")
        rows = asyncio.run(scrape_depop(query, HEADLESS, DEEP_FETCH, limits, log_cb))
        st.write(f"Done. Total rows: **{len(rows)}**")

        # Save to Google Sheets
        try:
            ws = open_worksheet(SHEET_NAME, query, RESET_SHEET)
            save_to_google_sheets(ws, rows)
            st.write(f"✅ Saved {len(rows)} rows to **{SHEET_NAME} / {ws.title}**")
        except Exception as e:
            st.warning(f"Could not write to Google Sheets: {e}")

        if rows:
            st.dataframe(rows[:200])
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(SHEET_HEADERS)
            for r in rows:
                writer.writerow([
                    r.get("platform","Depop"),
                    r.get("brand",""),
                    r.get("item_name",""),
                    r.get("price",""),
                    r.get("size",""),
                    r.get("condition",""),
                    r.get("link",""),
                ])
            st.download_button("Download CSV",
                               data=output.getvalue().encode("utf-8"),
                               file_name=f"depop_{query.replace(' ','_')}.csv",
                               mime="text/csv")
        status.update(label="Scrape complete", state="complete")
