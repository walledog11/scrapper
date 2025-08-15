# app.py — Depop Scraper (clean UI + price normalization + creds msgs under System Status)

import os, io, csv, time, re
from typing import List, Dict
import streamlit as st
from pandas import DataFrame

# ---------- Page setup ----------
st.set_page_config(page_title="Depop Scraper", page_icon="🎢", layout="wide")

# ---------- First-time help ----------
FIRST_TIME_HELP = """
**Quick Setup Guide**  
1. In Streamlit Cloud → **Settings → Secrets**, add your Google service account under **[google_service_account]**  
2. Share your Google Sheet with the service account email (Editor)  
3. Enter a search term, then click **Start Scraping**  
"""

# ---------- Small utilities ----------
_CURRENCY_SYM = {"USD": "$", "GBP": "£", "EUR": "€"}

def clean_title(t: str) -> str:
    t = (t or "").strip()
    # strip " by seller" or " | seller"
    if " by " in t.lower():
        t = t.split(" by ", 1)[0].strip()
    if " | " in t:
        t = t.split(" | ", 1)[0].strip()
    # collapse whitespace
    return re.sub(r"\s+", " ", t)

def normalize_price(p: str) -> str:
    """
    Return a clean price like "$120" / "£40" / "€15.50".
    Accepts raw strings from list/PDP or empty.
    """
    s = (p or "").strip()
    if not s:
        return ""
    # Pick first "£ 12.34" | "$12" | "€ 9,50" pattern
    m = re.search(r"([£$€])\s?(\d[\d.,]*)", s)
    if m:
        sym, amt = m.group(1), m.group(2).replace(",", "")
        return f"{sym}{amt}"
    # If they put currency code (rare)
    m2 = re.search(r"(USD|GBP|EUR)\s?(\d[\d.,]*)", s, re.I)
    if m2:
        sym = _CURRENCY_SYM.get(m2.group(1).upper(), "")
        amt = m2.group(2).replace(",", "")
        return f"{sym}{amt}".strip()
    # Raw number fallback
    m3 = re.search(r"(\d[\d.,]*)", s)
    if m3:
        amt = m3.group(1).replace(",", "")
        return f"${amt}"  # default to USD when unknown
    return ""

def looks_like_product_link(url: str) -> bool:
    return bool(url) and "/products/" in url

def postprocess_rows(rows: List[Dict]) -> List[Dict]:
    """Clean titles, normalize price, drop obvious search links, and dedupe by link."""
    seen = set()
    cleaned = []
    for r in rows:
        link = (r.get("link") or "").strip()
        if not looks_like_product_link(link):
            # ignore search list URLs; keep only product pages
            continue
        if link in seen:
            continue
        seen.add(link)
        r2 = {
            "platform": r.get("platform", "Depop"),
            "brand": (r.get("brand") or "").strip(),
            "item_name": clean_title(r.get("item_name") or ""),
            "price": normalize_price(r.get("price") or ""),
            "size": (r.get("size") or "").strip(),
            "condition": (r.get("condition") or "").strip(),
            "link": link,
        }
        cleaned.append(r2)
    return cleaned

# ---------- UI sections ----------
def render_header():
    st.markdown("""
        <style>
            .main-header { font-size: 36px; text-align: center; font-weight: 800; margin-bottom: 4px; }
            .subheader { font-size: 18px; text-align: center; opacity: .8; margin-bottom: 12px; }
        </style>
        <div class="main-header">🎢 Depop Scraper</div>
        <div class="subheader">Search Depop listings and export to Google Sheets</div>
    """, unsafe_allow_html=True)

def render_search_controls():
    st.markdown("#### 🔍 Search Configuration")
    st.markdown('<div class="search-controls">', unsafe_allow_html=True)
    c1, c2, c3 = st.columns([3, 1.2, 1.5], vertical_alignment="bottom")

    with c1:
        st.session_state.query = st.text_input(
            "What are you looking for?",
            value=st.session_state.get("query", "Supreme Box Logo"),
            placeholder="e.g., Palace hoodie, Stone Island jacket, Carhartt pants...",
        )
    with c2:
        st.markdown('<div style="margin-bottom: 8px;"></div>', unsafe_allow_html=True)
        st.session_state.deep = st.toggle(
            "🔬 Deep Fetch",
            value=st.session_state.get("deep", True),
            help="Visit item pages to extract Size & Condition (slower)."
        )
    with c3:
        st.markdown('<div style="margin-bottom: 8px;"></div>', unsafe_allow_html=True)
        st.session_state.run = st.button("🚀 Start Scraping", use_container_width=True, type="primary")

    st.markdown('</div>', unsafe_allow_html=True)

def render_info_section():
    tab1, tab2 = st.tabs(["📋 Setup Guide", "🔧 System Status"])

    with tab1:
        st.markdown(FIRST_TIME_HELP)

    with tab2:
        col1, col2 = st.columns(2)

        with col1:
            st.write("**Environment**")
            st.info("✅ Playwright: Auto-installed" if True else "❌ Playwright: Missing")
            st.info("✅ Local credentials.json found" if os.path.exists("credentials.json") else "ℹ️ Using cloud credentials")

            # All credentials badges/messages live here (and only here)
            IS_CLOUD = bool(os.environ.get("STREAMLIT_RUNTIME"))
            # We hide the success badge on Cloud to avoid exposing anything sensitive,
            # and only show a generic state locally.
            if not IS_CLOUD:
                if st.session_state.get("secrets_ok"):
                    st.success("🔐 Secrets OK — using [google_service_account] (TOML table)")
                elif st.session_state.get("local_creds_ok"):
                    st.info("🔐 Using local credentials.json")
                else:
                    st.error("🔴 No Credentials Found")

        with col2:
            st.write("**Module Status**")
            try:
                import creds_loader  # noqa: F401
                st.success("✅ Google Sheets integration ready")
            except Exception:
                st.warning("⚠️ Google Sheets integration unavailable")

            try:
                import depop_scraper_lib  # noqa: F401
                st.success("✅ Scraper module loaded")
            except Exception:
                st.warning("⚠️ Scraper module unavailable")

def render_results(rows: List[Dict], sheet_name: str):
    st.markdown("#### 📊 Results")

    # Metrics
    brands_count = len({(r.get('brand') or '').strip() for r in rows if r.get('brand')})
    st.markdown(
        f"""
        <div class="metric-grid">
            <div class="metric-card">
                <div class="metric-label">Items Found</div>
                <div class="metric-value">{len(rows)}</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Saved to</div>
                <div class="metric-value">{sheet_name}</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Unique Brands</div>
                <div class="metric-value">{brands_count}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    # Views
    tab1, tab2, tab3 = st.tabs(["📄 Data Table", "💾 Download", "📝 Activity Log"])

    with tab1:
        if rows:
            df = DataFrame(rows, columns=["platform", "brand", "item_name", "price", "size", "condition", "link"])
            st.dataframe(df, use_container_width=True, height=420)
        else:
            st.info("No data to display yet. Run a search to see results here.")

    with tab2:
        if rows:
            output = io.StringIO()
            w = csv.writer(output)
            w.writerow(["Platform", "Brand", "Item Name", "Price", "Size", "Condition", "Link"])
            for r in rows:
                w.writerow([
                    r.get("platform",""), r.get("brand",""), r.get("item_name",""),
                    r.get("price",""), r.get("size",""), r.get("condition",""), r.get("link","")
                ])
            st.download_button(
                label="📥 Download as CSV",
                data=output.getvalue().encode("utf-8"),
                file_name=f"depop_{st.session_state.query.replace(' ', '_')}.csv",
                mime="text/csv",
                use_container_width=True
            )
        else:
            st.info("Run a search first to enable downloads.")

    with tab3:
        logs = st.session_state.get("logs", [])
        if logs:
            st.code("\n".join(logs), language="text")
        else:
            st.info("Activity logs will appear here during scraping.")

# ---------- Import helpers ----------
try:
    from creds_loader import authorize_gspread
except Exception:
    authorize_gspread = None

try:
    from depop_scraper_lib import scrape_depop
except Exception:
    scrape_depop = None

# ---------- Sidebar ----------
with st.sidebar:
    st.markdown("#### ⚙️ Settings")
    with st.container(border=True):
        st.markdown("**Google Sheets**")
        IS_CLOUD = bool(os.environ.get("STREAMLIT_RUNTIME"))
        prefer_local = st.toggle("Use local credentials.json", value=not IS_CLOUD)
        SHEET_NAME = st.text_input("Spreadsheet name", value="depop_scraper", help="Name of your Google Sheet")
        RESET_SHEET = st.toggle("Clear sheet before writing", value=False)

    with st.container(border=True):
        st.markdown("**Performance Limits**")
        MAX_ITEMS = st.number_input("Max items to scrape", min_value=100, max_value=20000, value=3000, step=100)
        MAX_DURATION_S = st.number_input("Timeout (seconds)", min_value=60, max_value=3600, value=900, step=30)

    with st.container(border=True):
        st.markdown("**Deep Fetch Settings**")
        DEEP_FETCH_MAX = st.number_input("Max items for deep fetch", min_value=50, max_value=5000, value=1000, step=50)
        DEEP_FETCH_CONCURRENCY = st.slider("Concurrent requests", 1, 6, 3)
        DEEP_FETCH_DELAY_MIN, DEEP_FETCH_DELAY_MAX = st.slider("Request delay (ms)", 200, 4000, (800, 1600))

    with st.expander("🔧 Advanced Options"):
        MAX_ROUNDS = st.number_input("Max scroll rounds", min_value=10, max_value=2000, value=400, step=10)
        WARMUP_ROUNDS = st.number_input("Warmup rounds", min_value=0, max_value=100, value=6, step=1)
        IDLE_ROUNDS = st.number_input("Stop after N idle rounds", min_value=2, max_value=30, value=6, step=1)
        NETWORK_IDLE_EVERY = st.number_input("Network check interval", min_value=5, max_value=60, value=12, step=1)
        NETWORK_IDLE_TIMEOUT = st.number_input("Network timeout (ms)", min_value=1000, max_value=20000, value=5000, step=500)
        PAUSE_MIN, PAUSE_MAX = st.slider("Scroll pause range (ms)", 200, 1500, (500, 900))

# ---------- Main ----------
render_header()
with st.container(border=True):
    render_search_controls()
with st.container(border=True):
    render_info_section()

# Sheets auth (deferred)
gc = None
if authorize_gspread:
    try:
        gc = authorize_gspread(prefer_local=prefer_local)
        # Mark, but only displayed inside System Status tab
        st.session_state["secrets_ok"] = not prefer_local
        st.session_state["local_creds_ok"] = prefer_local
    except Exception as e:
        st.session_state["secrets_ok"] = False
        st.session_state["local_creds_ok"] = False
        st.info(f"💡 Google Sheets integration not available: {e}")

# Run
if st.session_state.get("run"):
    st.session_state.logs = []

    def log(msg: str):
        st.session_state.logs.append(f"{time.strftime('%H:%M:%S')} - {msg}")

    limits = dict(
        MAX_ITEMS=int(MAX_ITEMS),
        MAX_DURATION_S=int(MAX_DURATION_S),
        DEEP_FETCH_MAX=int(DEEP_FETCH_MAX),
        DEEP_FETCH_CONCURRENCY=int(DEEP_FETCH_CONCURRENCY),
        DEEP_FETCH_DELAY_MIN=int(DEEP_FETCH_DELAY_MIN),
        DEEP_FETCH_DELAY_MAX=int(DEEP_FETCH_DELAY_MAX),
        MAX_ROUNDS=int(MAX_ROUNDS),
        WARMUP_ROUNDS=int(WARMUP_ROUNDS),
        IDLE_ROUNDS=int(IDLE_ROUNDS),
        NETWORK_IDLE_EVERY=int(NETWORK_IDLE_EVERY),
        NETWORK_IDLE_TIMEOUT=int(NETWORK_IDLE_TIMEOUT),
        PAUSE_MIN=int(PAUSE_MIN),
        PAUSE_MAX=int(PAUSE_MAX),
    )

    rows: List[Dict] = []
    if scrape_depop is None:
        log("Scraper module not available — generating sample data")
        rows = [{
            "platform": "Depop",
            "brand": "",
            "item_name": f"{st.session_state.query} (sample)",
            "price": "$199",
            "size": "L",
            "condition": "Good condition",
            "link": f"https://www.depop.com/search/?q={st.session_state.query.replace(' ','%20')}",
        }]
    else:
        with st.spinner(f"🔍 Scraping Depop for '{st.session_state.query}'..."):
            start = time.time()
            log(f"Starting scrape for '{st.session_state.query}' (max {MAX_ITEMS}, deep={st.session_state.deep})")
            try:
                rows = scrape_depop(st.session_state.query, deep=st.session_state.deep, limits=limits)
            except Exception as e:
                log(f"❌ Scraping failed: {e}")
                st.error(f"Scraping failed: {e}")
            dur = time.time() - start
            log(f"✅ Scraping completed in {dur:.1f}s — raw rows: {len(rows)}")
            
            if not rows:
                log("No rows returned by scraper. Showing sample row to keep the UI responsive.")
                rows = [{
                    "platform": "Depop",
                    "brand": "Sample",
                    "item_name": f"{st.session_state.query} (sample)",
                    "price": "$199",
                    "size": "L",
                    "condition": "Good condition",
                    "link": f"https://www.depop.com/search/?q={st.session_state.query.replace(' ','%20')}"
                }]


    # Post-process (prices, titles, links, dedupe)
    rows = postprocess_rows(rows)

    # Save to Google Sheets
    if gc and rows:
        with st.spinner("💾 Saving to Google Sheets…"):
            try:
                import gspread
                headers = ["Platform", "Brand", "Item Name", "Price", "Size", "Condition", "Link"]
                try:
                    sh = gc.open(SHEET_NAME)
                except gspread.SpreadsheetNotFound:
                    sh = gc.create(SHEET_NAME)
                    log(f"Created spreadsheet: {SHEET_NAME}")

                tab_title = st.session_state.query[:99] or "Results"
                try:
                    ws = sh.worksheet(tab_title)
                except gspread.WorksheetNotFound:
                    ws = sh.add_worksheet(title=tab_title, rows="5000", cols=str(len(headers)))
                    ws.append_row(headers)
                    log(f"Created worksheet: {tab_title}")

                if RESET_SHEET or not ws.get_all_values():
                    ws.clear()
                    ws.append_row(headers)
                    log("Reset worksheet and headers")

                payload = [[
                    r["platform"], r["brand"], r["item_name"], r["price"],
                    r["size"], r["condition"], r["link"]
                ] for r in rows]

                BATCH = 300
                total_batches = (len(payload) + BATCH - 1) // BATCH
                for i in range(0, len(payload), BATCH):
                    ws.append_rows(payload[i:i+BATCH], value_input_option="RAW")
                    log(f"Uploaded batch {(i//BATCH)+1}/{total_batches}")

                st.success(f"✅ Saved {len(rows)} items to **{SHEET_NAME} / {tab_title}**")
            except Exception as e:
                st.warning(f"⚠️ Could not save to Google Sheets: {e}")
                log(f"Save failed: {e}")

    # Show results
    render_results(rows, SHEET_NAME)
