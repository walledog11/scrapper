# app.py — Streamlit + Playwright Depop scraper (Cloud-ready)

import os, sys, json, asyncio, random, time, urllib.parse, io, csv, subprocess
from typing import List, Dict

import streamlit as st
from gspread.exceptions import APIError

# ---- External modules in your repo ----
# Expect these files to be present:
#   - creds_loader.py   (provides authorize_gspread)
#   - depop_scraper_lib.py (provides scrape_depop)
try:
    from creds_loader import authorize_gspread
except Exception as e:
    authorize_gspread = None
    _CRED_ERR = e

try:
    from depop_scraper_lib import scrape_depop
except Exception as e:
    scrape_depop = None
    _SCRAPER_ERR = e

# ------------- Page config -------------
st.set_page_config(page_title="Depop Scraper", page_icon="🧢", layout="wide")
print(f"[HEARTBEAT] {time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime())} app starting")

# ------------- Sidebar (leave as-is conceptually) -------------
st.sidebar.header("Settings")

# Prefer local creds toggle (overridden to False on Cloud)
prefer_local = st.sidebar.toggle("Prefer local credentials.json", value=True,
                                 help="If off, use Streamlit Secrets. On Cloud this is forced off.")

# Headless is recommended on Cloud
deep_fetch = st.sidebar.toggle("Deep fetch product pages (Size/Condition)", value=True)
sheet_name = st.sidebar.text_input("Google Sheet name", value="depop_scraper",
                                   help="Spreadsheet document name (not the tab name).")

st.sidebar.subheader("Limits")
max_items = st.sidebar.number_input("Max items to fetch", min_value=50, max_value=20000, value=1000, step=50)
max_seconds = st.sidebar.number_input("Max duration (seconds)", min_value=60, max_value=3600, value=900, step=30)
deep_fetch_max = st.sidebar.number_input("Max deep-fetched items", min_value=50, max_value=5000, value=1200, step=50)
deep_fetch_conc = st.sidebar.slider("Deep fetch concurrency", 1, 6, 3)
pause_min, pause_max = st.sidebar.slider("Jitter between scrolls (ms)", 200, 1500, (500, 900))

st.sidebar.subheader("Advanced scroll")
max_rounds = st.sidebar.number_input("Max scroll rounds", min_value=10, max_value=2000, value=400, step=10)
warmup_rounds = st.sidebar.number_input("Warmup rounds", min_value=0, max_value=100, value=6, step=1)
idle_rounds = st.sidebar.number_input("Stop if no growth for N rounds", min_value=2, max_value=30, value=6, step=1)
net_idle_every = st.sidebar.number_input("Wait for network-idle every N rounds", min_value=5, max_value=60, value=12, step=1)
net_idle_timeout = st.sidebar.number_input("Network-idle timeout (ms)", min_value=1000, max_value=20000, value=5000, step=500)
detail_delay_min, detail_delay_max = st.sidebar.slider("Per detail page delay (ms)", 200, 4000, (800, 1600))

# On Streamlit Cloud always use secrets (not local creds)
IS_CLOUD = bool(os.environ.get("STREAMLIT_RUNTIME"))
if IS_CLOUD:
    prefer_local = False

# ------------- Main header -------------
st.title("🧢 Depop Scraper")

with st.expander("First time? (quick setup)", expanded=False):
    st.markdown(
        """
**Local run**
1) `python3 -m venv .venv && source .venv/bin/activate`  
2) `pip install -r requirements.txt`  
3) `python -m playwright install chromium`  
4) Put your Google service account JSON as `credentials.json` **or** add it to `.streamlit/secrets.toml`.

**Streamlit Cloud**
1) Add your Google creds to **Secrets** as `[google_service_account]` (TOML table with triple-quoted `private_key`).  
2) (Optional) `packages.txt` should list required apt libs (one per line, no comments).  
3) Deploy. The app will auto-install browsers at runtime.
        """
    )

with st.expander("🔍 Health check", expanded=True):
    # Secrets presence checks (don’t raise; only display)
    has_google_table = False
    try:
        has_google_table = "google_service_account" in st.secrets
    except Exception:
        pass
    st.write("**Running in Cloud**:", "✅" if IS_CLOUD else "💻 Local")
    st.write("**credentials.json present?**", "✅" if os.path.exists("credentials.json") else "❌")
    st.write("**[google_service_account] in Secrets?**", "✅" if has_google_table else "❌")

    # Try to ensure Playwright browsers (best effort, non-fatal)
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium", "--with-deps"],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        st.write("**Playwright Chromium ensured**: ✅")
    except Exception as e:
        st.write("**Playwright Chromium ensured**: ⚠️", e)

# ------------- Inputs + button -------------
st.divider()
colA, colB = st.columns([3,1])
with colA:
    search_term = st.text_input("Search term", value="Supreme Box Logo")
with colB:
    run_btn = st.button("Run scrape 🚀", type="primary")

# ------------- Google Sheets helpers -------------
SHEET_HEADERS = ["Platform", "Brand", "Item Name", "Price", "Size", "Condition", "Link"]

def ensure_worksheet(gc, doc_name: str, title: str):
    import gspread
    try:
        sh = gc.open(doc_name)
    except gspread.SpreadsheetNotFound:
        sh = gc.create(doc_name)
    # Tab is the search term
    try:
        ws = sh.worksheet(title[:99])
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=title[:99], rows="5000", cols=str(len(SHEET_HEADERS)))
        ws.append_row(SHEET_HEADERS)
        return ws
    # Ensure headers
    vals = ws.get_all_values()
    if not vals or vals[0] != SHEET_HEADERS:
        ws.clear()
        ws.append_row(SHEET_HEADERS)
    return ws

# --- Quota-friendly Sheets writer (batch + backoff + de-dupe) ---
def save_to_google_sheets(ws, rows, batch_size=200, max_retries=6, progress=None):
    """
    rows: list[dict] with keys: platform, brand, item_name, price, size, condition, link
    - De-dupes by Link against the sheet
    - Batches writes to reduce API calls
    - Retries 429 rate limits with exponential backoff + jitter
    """
    if not rows:
        return 0

    try:
        existing_links = set(ws.col_values(7)[1:])  # col 7 = Link
    except Exception:
        existing_links = set()

    payload = [[
        r.get("platform","Depop"),
        r.get("brand",""),
        r.get("item_name",""),
        r.get("price",""),
        r.get("size",""),
        r.get("condition",""),
        r.get("link",""),
    ] for r in rows if r.get("link") and r["link"] not in existing_links]

    if not payload:
        return 0

    written = 0
    total = len(payload)
    for i in range(0, total, batch_size):
        chunk = payload[i:i + batch_size]
        attempt = 0
        while True:
            try:
                ws.append_rows(chunk, value_input_option="RAW")
                written += len(chunk)
                time.sleep(0.4 + random.uniform(0.0, 0.3))  # tiny pause to smooth bursts
                break
            except APIError as e:
                is_429 = ("Quota exceeded" in str(e)) or ("Rate Limit Exceeded" in str(e)) or ("[429]" in str(e))
                if not is_429 or attempt >= max_retries:
                    raise
                sleep_s = min(60, 2 ** attempt) + random.uniform(0.2, 1.3)
                attempt += 1
                if progress:
                    try:
                        progress.progress(
                            min(1.0, (i + written) / max(total, 1)),
                            text=f"Sheets throttled (429). Retry {attempt}/{max_retries} in {sleep_s:.1f}s…"
                        )
                    except Exception:
                        pass
                time.sleep(sleep_s)

        if progress:
            try:
                progress.progress(min(1.0, (i + len(chunk)) / max(total, 1)),
                                  text=f"Wrote {i + len(chunk)}/{total} rows…")
            except Exception:
                pass

    return written

# ------------- Run action -------------
if run_btn:
    # Limits to pass to scraper
    limits = dict(
        MAX_ITEMS=int(max_items),
        MAX_DURATION_S=int(max_seconds),
        DEEP_FETCH_MAX=int(deep_fetch_max),
        DEEP_FETCH_CONCURRENCY=int(deep_fetch_conc),
        PAUSE_MIN=int(pause_min),
        PAUSE_MAX=int(pause_max),
        MAX_ROUNDS=int(max_rounds),
        WARMUP_ROUNDS=int(warmup_rounds),
        IDLE_ROUNDS=int(idle_rounds),
        NETWORK_IDLE_EVERY=int(net_idle_every),
        NETWORK_IDLE_TIMEOUT=int(net_idle_timeout),
        DEEP_FETCH_DELAY_MIN=int(detail_delay_min),
        DEEP_FETCH_DELAY_MAX=int(detail_delay_max),
    )

    # Try to authorize Sheets (non-fatal: we can still show results + CSV)
    gc = None
    try:
        if authorize_gspread is None:
            raise RuntimeError(f"creds_loader not importable: {_CRED_ERR}")
        # On Cloud, prefer_local already forced False above
        st.write("Authorizing Google Sheets…")
        gc = authorize_gspread(prefer_local=prefer_local)
        st.success("Sheets auth OK ✅")
    except Exception as e:
        st.warning(f"Sheets auth not ready (UI continues): {e}")

    # Run scraper
    with st.status("Scraping…", expanded=True) as status:
        st.write(f"Starting scrape for **{search_term}** (max {max_items}, deep={deep_fetch})")
        try:
            if scrape_depop is None:
                raise RuntimeError(f"Could not import scraper module: {_SCRAPER_ERR}")
            rows = scrape_depop(search_term, deep=deep_fetch, limits=limits)
            st.write(f"Done. Total rows: **{len(rows)}**")
        except Exception as e:
            st.error("Scrape failed:")
            st.exception(e)
            rows = []

        # Save to Sheets if authorized
        if rows and gc:
            try:
                ws = ensure_worksheet(gc, sheet_name, search_term)
                prog = st.progress(0.0, text="Writing to Google Sheets…")
                written = save_to_google_sheets(ws, rows, batch_size=200, max_retries=6, progress=prog)
                prog.empty()
                st.success(f"✅ Saved {written} new rows to **{sheet_name} / {ws.title}**")
            except Exception as e:
                st.warning(f"Could not write to Google Sheets: {e}")

        # Always show a preview & CSV
        if rows:
            st.dataframe(rows[:300])
            # CSV download
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["Platform","Brand","Item Name","Price","Size","Condition","Link"])
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
            st.download_button(
                "Download CSV",
                data=output.getvalue().encode("utf-8"),
                file_name=f"depop_{search_term.replace(' ','_')}.csv",
                mime="text/csv"
            )

        status.update(label="Scrape complete", state="complete")
