import os, io, csv, time
from typing import List, Dict
import streamlit as st
from pandas import DataFrame

# --- Page setup
st.set_page_config(page_title="Depop Scraper", page_icon="🧢", layout="wide")

# --- Custom CSS for theme support, alignment, and slider containment
st.markdown("""
    <style>
        /* Theme-aware global styles */
        .stApp {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background-color: var(--background-color, #f7f9fc) !important;
            color: var(--text-color, #1e293b) !important;
        }
        [data-testid="stAppViewContainer"] {
            background-color: var(--background-color, #f7f9fc) !important;
        }
        [data-testid="stBaseButton-primary"] {
            background-color: var(--primary-color, #3b82f6) !important;
            color: var(--button-text, white) !important;
            border-radius: 8px !important;
            padding: 10px 20px !important;
            font-weight: 500 !important;
            border: none !important;
            transition: background-color 0.2s !important;
        }
        [data-testid="stBaseButton-primary"]:hover {
            background-color: var(--primary-hover, #2563eb) !important;
        }
        .main-header {
            font-size: 28px;
            font-weight: 600;
            color: var(--text-color, #1e293b) !important;
            margin-bottom: 8px;
        }
        .subheader {
            font-size: 14px;
            color: var(--secondary-text-color, #64748b) !important;
            margin-bottom: 16px;
        }
        .status-badge {
            font-size: 12px;
            padding: 6px 12px;
            border-radius: 12px;
            color: var(--badge-text, white) !important;
            display: inline-block;
        }
        .status-ok { background-color: #22c55e; }
        .status-local { background-color: #f59e0b; }
        .status-error { background-color: #ef4444; }
        .stTextInput > div > input {
            border-radius: 8px;
            border: 1px solid var(--border-color, #e2e8f0) !important;
            padding: 10px;
            background-color: var(--input-bg, #ffffff) !important;
            color: var(--text-color, #1e293b) !important;
        }
        .stToggle > label {
            font-size: 14px;
            color: var(--text-color, #1e293b) !important;
        }
        .stNumberInput > div > input, .stSlider > div {
            border-radius: 8px;
            border: 1px solid var(--border-color, #e2e8f0) !important;
            background-color: var(--input-bg, #ffffff) !important;
            color: var(--text-color, #1e293b) !important;
        }
        .stTabs [role="tab"] {
            border-radius: 8px;
            padding: 8px 16px;
            font-weight: 500;
            color: var(--text-color, #1e293b) !important;
            background-color: var(--tab-bg, #f1f5f9) !important;
        }
        .stTabs [role="tab"][aria-selected="true"] {
            background-color: var(--primary-color, #3b82f6) !important;
            color: var(--button-text, white) !important;
        }
        .card {
            background-color: var(--card-bg, #ffffff) !important;
            border-radius: 12px;
            padding: 20px;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
            margin-bottom: 20px;
        }
        .metric-card {
            background-color: var(--card-bg, #ffffff) !important;
            border-radius: 8px;
            padding: 16px;
            text-align: center;
            box-shadow: 0 1px 4px rgba(0, 0, 0, 0.05);
        }
        .metric-label {
            font-size: 14px;
            color: var(--secondary-text-color, #64748b) !important;
            margin-bottom: 4px;
        }
        .metric-value {
            font-size: 20px;
            font-weight: 600;
            color: var(--text-color, #1e293b) !important;
        }
        [data-testid="stSidebar"] .stToggle > label,
        [data-testid="stSidebar"] .stNumberInput > label,
        [data-testid="stSidebar"] .stSlider > label {
            font-weight: 500;
            color: var(--text-color, #1e293b) !important;
        }
        .stDataFrame {
            border-radius: 8px;
            overflow: hidden;
            background-color: var(--card-bg, #ffffff) !important;
            color: var(--text-color, #1e293b) !important;
        }
        .stCodeBlock {
            background-color: var(--card-bg, #ffffff) !important;
            color: var(--text-color, #1e293b) !important;
        }
        [data-testid="stDownloadButton"] > button {
            background-color: var(--download-bg, #10b981) !important;
            color: var(--button-text, white) !important;
            border-radius: 8px !important;
            padding: 10px 20px !important;
            font-weight: 500 !important;
        }
        [data-testid="stDownloadButton"] > button:hover {
            background-color: var(--download-hover, #059669) !important;
        }
        /* Align search bar, toggle, and button */
        .control-row {
            display: flex;
            align-items: center;
            gap: 16px;
            flex-wrap: nowrap;
            margin-top: 12px;
        }
        .control-row .stTextInput {
            flex: 3;
            min-width: 200px;
        }
        .control-row .stToggle {
            flex: 1;
            min-width: 100px;
        }
        .control-row .stButton {
            flex: 1;
            min-width: 120px;
        }
        /* Sidebar slider spacing and containment */
        [data-testid="stSidebar"] .stSlider {
            margin-bottom: 16px;
            padding-right: 4px; /* Reduced padding */
            max-width: 280px; /* Fixed width to fit sidebar */
            width: 100%;
            box-sizing: border-box;
            overflow: hidden;
        }
        [data-testid="stSidebar"] .stNumberInput {
            margin-bottom: 16px;
            max-width: 280px;
            width: 100%;
            box-sizing: border-box;
        }
        [data-testid="stSidebar"] .stToggle {
            margin-bottom: 16px;
            max-width: 280px;
            width: 100%;
            box-sizing: border-box;
        }
        [data-testid="stSidebar"] .stTextInput {
            margin-bottom: 16px;
            max-width: 280px;
            width: 100%;
            box-sizing: border-box;
        }
        /* Sidebar container wrapper */
        [data-testid="stSidebar"] > div:first-child {
            overflow-x: hidden;
            padding: 10px;
        }
        /* Dark mode fixes and enhanced contrast */
        [data-theme="light"] {
            --background-color: #f7f9fc;
            --text-color: #1e293b;
            --secondary-text-color: #64748b;
            --border-color: #e2e8f0;
            --input-bg: #ffffff;
            --card-bg: #ffffff;
            --tab-bg: #f1f5f9;
            --primary-color: #3b82f6;
            --primary-hover: #2563eb;
            --download-bg: #10b981;
            --download-hover: #059669;
            --button-text: white;
            --badge-text: white;
        }
        [data-theme="dark"] {
            --background-color: #1e293b;
            --text-color: #f1f5f9;
            --secondary-text-color: #94a3b8;
            --border-color: #475569;
            --input-bg: #334155;
            --card-bg: #2d3748;
            --tab-bg: #374151;
            --primary-color: #60a5fa;
            --primary-hover: #3b82f6;
            --download-bg: #34d399;
            --download-hover: #10b981;
            --button-text: white;
            --badge-text: white;
        }
        /* Ensure dark mode text visibility */
        [data-theme="dark"] .stApp, [data-theme="dark"] .stTextInput > div > input,
        [data-theme="dark"] .stNumberInput > div > input, [data-theme="dark"] .stSlider > div,
        [data-theme="dark"] .stDataFrame, [data-theme="dark"] .stCodeBlock {
            color: var(--text-color, #f1f5f9) !important;
            background-color: var(--input-bg, #334155) !important;
        }
        [data-theme="dark"] .stTabs [role="tab"] {
            background-color: var(--tab-bg, #374151) !important;
        }
        [data-theme="dark"] .card, [data-theme="dark"] .metric-card {
            background-color: var(--card-bg, #2d3748) !important;
        }
    </style>
""", unsafe_allow_html=True)

# --- First-time help text
FIRST_TIME_HELP = """
**First Time Setup**  
1. In Streamlit Cloud → **Settings → Secrets**, add your Google service account under `[google_service_account]` with a triple-quoted `private_key`.  
2. Share your target Google Sheet with the service account email (Editor).  
3. Run a search to write results to your sheet.
"""

# --- Helpers to render UI sections
def render_header():
    with st.container():
        st.markdown('<div class="main-header">🧢 Depop Scraper</div>', unsafe_allow_html=True)
        st.markdown('<div class="subheader">Search Depop listings, fetch size & condition, and export to Google Sheets.</div>', unsafe_allow_html=True)
        status = (
            '<span class="status-badge status-ok">🟢 Secrets OK</span>' if st.session_state.get("secrets_ok")
            else '<span class="status-badge status-local">🟡 Local creds</span>' if st.session_state.get("local_creds_ok")
            else '<span class="status-badge status-error">🔴 No creds</span>'
        )
        st.markdown(status, unsafe_allow_html=True)

def render_controls():
    with st.container(border=True):
        st.markdown("#### Search")
        # Use custom div for flexbox alignment
        st.markdown('<div class="control-row">', unsafe_allow_html=True)
        c1, c2, c3 = st.columns([3, 1, 1], vertical_alignment="center")
        with c1:
            st.session_state.query = st.text_input(
                "Search term",
                value=st.session_state.get("query", "Supreme Box Logo"),
                placeholder="e.g., palace hoodie, arcteryx alpha...",
                label_visibility="collapsed"
            )
        with c2:
            st.session_state.deep = st.toggle(
                "Deep fetch",
                value=st.session_state.get("deep", True),
                help="Visit item pages to extract Size & Condition."
            )
        with c3:
            st.session_state.run = st.button("🚀 Run Scrape", use_container_width=True, type="primary")
        st.markdown('</div>', unsafe_allow_html=True)

def render_health():
    with st.container(border=True):
        st.markdown("#### Info")
        ft, health = st.tabs(["🧭 First Time?", "🩺 Health Check"])
        with ft:
            st.markdown(FIRST_TIME_HELP)
        with health:
            st.info("**Playwright**: Auto-installed at runtime on the cloud.")
            st.info("**Google Sheets**: Service Account from secrets or local credentials.json.")
            st.write("**credentials.json present?**", os.path.exists("credentials.json"))
            try:
                import creds_loader  # noqa: F401
                st.success("✅ creds_loader imported")
            except Exception as e:
                st.warning(f"⚠️ creds_loader import failed: {e}")
            try:
                import depop_scraper_lib  # noqa: F401
                st.success("✅ depop_scraper_lib imported")
            except Exception as e:
                st.warning(f"⚠️ depop_scraper_lib import failed: {e}")

def render_results(rows: List[Dict], sheet_name: str):
    with st.container(border=True):
        st.markdown("#### Results")
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown('<div class="metric-card"><div class="metric-label">Items Scraped</div><div class="metric-value">{}</div></div>'.format(len(rows)), unsafe_allow_html=True)
        with c2:
            st.markdown('<div class="metric-card"><div class="metric-label">Saved to Sheets</div><div class="metric-value">{}</div></div>'.format(sheet_name), unsafe_allow_html=True)
        with c3:
            brands = len({r.get('brand','').strip() for r in rows if r.get('brand')})
            st.markdown('<div class="metric-card"><div class="metric-label">Brand Coverage</div><div class="metric-value">{}</div></div>'.format(brands), unsafe_allow_html=True)

        tabs = st.tabs(["📄 Table", "📥 Download CSV", "🪵 Logs"])
        with tabs[0]:
            if rows:
                df = DataFrame(rows, columns=["platform", "brand", "item_name", "price", "size", "condition", "link"])
                st.dataframe(df, use_container_width=True, height=400)
            else:
                st.warning("No rows to display yet.")

        with tabs[1]:
            if rows:
                output = io.StringIO()
                w = csv.writer(output)
                w.writerow(["Platform", "Brand", "Item Name", "Price", "Size", "Condition", "Link"])
                for r in rows:
                    w.writerow([
                        r.get("platform", ""), r.get("brand", ""), r.get("item_name", ""),
                        r.get("price", ""), r.get("size", ""), r.get("condition", ""), r.get("link", "")
                    ])
                st.download_button(
                    "Download CSV",
                    output.getvalue().encode("utf-8"),
                    file_name=f"depop_{st.session_state.query.replace(' ', '_')}.csv",
                    mime="text/csv",
                    use_container_width=True
                )
            else:
                st.info("Run a scrape to enable download.")

        with tabs[2]:
            logs = st.session_state.get("logs", [])
            if logs:
                st.code("\n".join(logs), language="text")
            else:
                st.info("Logs will appear here while scraping.")

# --- Import helpers (Google Sheets auth + scraping)
try:
    from creds_loader import authorize_gspread
except Exception:
    authorize_gspread = None

try:
    from depop_scraper_lib import scrape_depop
except Exception:
    scrape_depop = None

# --- Sidebar
with st.sidebar:
    with st.container(border=True):
        st.markdown("#### Settings")
        IS_CLOUD = bool(os.environ.get("STREAMLIT_RUNTIME"))
        prefer_local = st.toggle("Prefer local credentials.json (debug)", value=not IS_CLOUD)
        SHEET_NAME = st.text_input("Google Sheet name", value="depop_scraper", help="Spreadsheet (doc) name")
        RESET_SHEET = st.toggle("Reset tab headers on write", value=False)

        st.markdown("##### Limits")
        MAX_ITEMS = st.number_input("Max items (safety cap)", min_value=100, max_value=20000, value=3000, step=100)
        MAX_DURATION_S = st.number_input("Max duration (seconds)", min_value=60, max_value=3600, value=900, step=30)

        st.markdown("##### Deep Fetch")
        DEEP_FETCH_MAX = st.number_input("Max deep-fetched items", min_value=50, max_value=5000, value=1000, step=50)
        DEEP_FETCH_CONCURRENCY = st.slider("Deep fetch concurrency", 1, 6, 3)
        DEEP_FETCH_DELAY_MIN, DEEP_FETCH_DELAY_MAX = st.slider("Per detail page delay (ms)", 200, 4000, (800, 1600))

        st.markdown("##### Advanced Scrolling")
        MAX_ROUNDS = st.number_input("Max scroll rounds", min_value=10, max_value=2000, value=400, step=10)
        WARMUP_ROUNDS = st.number_input("Warmup rounds", min_value=0, max_value=100, value=6, step=1)
        IDLE_ROUNDS = st.number_input("Stop if no growth for N rounds", min_value=2, max_value=30, value=6, step=1)
        NETWORK_IDLE_EVERY = st.number_input("Wait for network-idle every N rounds", min_value=5, max_value=60, value=12, step=1)
        NETWORK_IDLE_TIMEOUT = st.number_input("Network-idle timeout (ms)", min_value=1000, max_value=20000, value=5000, step=500)
        PAUSE_MIN, PAUSE_MAX = st.slider("Jitter between scrolls (ms)", 200, 1500, (500, 900))

# --- Main
render_header()
render_controls()
render_health()

# --- Attempt Google Sheets auth
gc = None
if authorize_gspread:
    try:
        gc = authorize_gspread(prefer_local=prefer_local)
        st.session_state["secrets_ok"] = not prefer_local
        st.session_state["local_creds_ok"] = prefer_local
    except Exception as e:
        st.session_state["secrets_ok"] = False
        st.session_state["local_creds_ok"] = False
        st.info(f"Sheets auth not ready (UI continues): {e}")

# --- Run scrape
if st.session_state.get("run"):
    st.session_state.logs = []
    def log(msg: str):
        st.session_state.logs.append(msg)

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
        log("Could not import scraper module — returning sample row.")
        rows = [{
            "platform":"Depop","brand":"Supreme","item_name":f"{st.session_state.query} (sample)",
            "price":"$199","size":"L","condition":"Good condition",
            "link": f"https://www.depop.com/search/?q={st.session_state.query.replace(' ','%20')}"
        }]
    else:
        log(f"Starting scrape for {st.session_state.query} (max {MAX_ITEMS}, deep={st.session_state.deep})")
        start = time.time()
        try:
            rows = scrape_depop(st.session_state.query, deep=st.session_state.deep, limits=limits)
        except Exception as e:
            log(f"Scrape error: {e}")
        dur = time.time() - start
        log(f"Finished in {dur:.1f}s, {len(rows)} rows.")

    if gc and rows:
        try:
            import gspread
            headers = ["Platform","Brand","Item Name","Price","Size","Condition","Link"]

            try:
                sh = gc.open(SHEET_NAME)
            except gspread.SpreadsheetNotFound:
                sh = gc.create(SHEET_NAME)

            tab_title = st.session_state.query[:99] or "Sheet1"
            try:
                ws = sh.worksheet(tab_title)
            except gspread.WorksheetNotFound:
                ws = sh.add_worksheet(title=tab_title, rows="5000", cols=str(len(headers)))
                ws.append_row(headers)

            if RESET_SHEET or not ws.get_all_values():
                ws.clear()
                ws.append_row(headers)

            payload = [[
                r.get("platform","Depop"),
                r.get("brand",""),
                r.get("item_name",""),
                r.get("price",""),
                r.get("size",""),
                r.get("condition",""),
                r.get("link",""),
            ] for r in rows]

            BATCH = 300
            for i in range(0, len(payload), BATCH):
                ws.append_rows(payload[i:i+BATCH], value_input_option="RAW")

            st.success(f"✅ Saved {len(rows)} rows to **{SHEET_NAME} / {tab_title}**")
        except Exception as e:
            st.warning(f"Could not write to Google Sheets: {e}")

    render_results(rows, SHEET_NAME)