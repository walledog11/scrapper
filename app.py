import os, io, csv, time
from typing import List, Dict
import streamlit as st
from pandas import DataFrame
import asyncio

# --- Page setup
st.set_page_config(page_title="Depop Scraper", page_icon="🎢", layout="wide")

# --- First-time help text
FIRST_TIME_HELP = """
**Quick Setup Guide**  
1. **Streamlit Cloud**: Go to Settings → Secrets and add your Google service account credentials under `[google_service_account]`
2. **Google Sheets**: Share your target spreadsheet with the service account email (give Editor permissions)  
3. **Run**: Enter a search term and click "Run Scrape" to start collecting data
"""

# --- Helpers to render UI sections
def render_header():
    st.markdown("""
        <style>
            .main-header {
                font-size: 36px;
                text-align: center;
                font-weight: bold;
            }
            .subheader {
                font-size: 20px;
                text-align: center;
            }
        </style>
        <div class="main-header">🎢 Depop Scraper</div>
        <div class="subheader">Search Depop listings and export to Google Sheets</div>
    """, unsafe_allow_html=True)

# --- UI Panels and Display
def render_search_controls():
    st.markdown("#### 🔍 Search Configuration")
    col1, col2, col3 = st.columns([3, 1.2, 1.5], vertical_alignment="bottom")

    with col1:
        st.session_state.query = st.text_input(
            "What are you looking for?",
            value=st.session_state.get("query", "Supreme Box Logo"),
            placeholder="e.g., Palace hoodie, Stone Island jacket, Carhartt pants...",
        )

    with col2:
        st.session_state.deep = st.toggle(
            "🔬 Deep Fetch",
            value=st.session_state.get("deep", True),
            help="Extract detailed size and condition data (slower but more complete)"
        )

    with col3:
        st.session_state.run = st.button("🚀 Start Scraping", use_container_width=True, type="primary")

def render_info_section():
    tab1, tab2 = st.tabs(["📋 Setup Guide", "🔧 System Status"])

    with tab1:
        st.markdown(FIRST_TIME_HELP)

    with tab2:
        col1, col2 = st.columns(2)

        with col1:
            st.write("**Environment**")
            st.info("✅ Playwright: Auto-installed")
            st.info("✅ Local credentials.json found" if os.path.exists("credentials.json") else "ℹ️ Using cloud credentials")

            # 🔐 Creds status only here (and hidden on Cloud)
            IS_CLOUD = bool(os.environ.get("STREAMLIT_RUNTIME"))
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

    st.markdown(f"""
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
            <div class="metric-value">{len({r.get('brand','').strip() for r in rows if r.get('brand')})}</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    tab1, tab2, tab3 = st.tabs(["📄 Data Table", "💾 Download", "📝 Activity Log"])

    with tab1:
        if rows:
            df = DataFrame(rows, columns=["platform", "brand", "item_name", "price", "size", "condition", "link"])
            st.dataframe(df, use_container_width=True, height=400)
        else:
            st.info("No data to display yet. Run a search to see results here.")

    with tab2:
        if rows:
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["Platform", "Brand", "Item Name", "Price", "Size", "Condition", "Link"])
            for row in rows:
                writer.writerow([
                    row.get("platform", ""), row.get("brand", ""), row.get("item_name", ""),
                    row.get("price", ""), row.get("size", ""), row.get("condition", ""), row.get("link", "")
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

# --- Import helpers
try:
    from creds_loader import authorize_gspread
except Exception:
    authorize_gspread = None

try:
    from depop_scraper_lib import scrape_depop
except Exception:
    scrape_depop = None

# --- Sidebar Configuration
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

        MAX_ROUNDS = st.number_input("Max scroll rounds", min_value=10, max_value=2000, value=400, step=10)
        WARMUP_ROUNDS = st.number_input("Warmup rounds", min_value=0, max_value=100, value=6, step=1)
        IDLE_ROUNDS = st.number_input("Stop after N idle rounds", min_value=2, max_value=30, value=6, step=1)
        NETWORK_IDLE_EVERY = st.number_input("Network check interval", min_value=5, max_value=60, value=12, step=1)
        NETWORK_IDLE_TIMEOUT = st.number_input("Network timeout (ms)", min_value=1000, max_value=20000, value=5000, step=500)
        PAUSE_MIN, PAUSE_MAX = st.slider("Scroll pause range (ms)", 200, 1500, (500, 900))

# --- Main Application Layout
render_header()
render_search_controls()
render_info_section()

# --- Google Sheets Authentication
gc = None
if authorize_gspread:
    try:
        gc = authorize_gspread(prefer_local=prefer_local)
        st.session_state["secrets_ok"] = not prefer_local
        st.session_state["local_creds_ok"] = prefer_local
    except Exception as e:
        st.session_state["secrets_ok"] = False
        st.session_state["local_creds_ok"] = False
        st.info(f"💡 Google Sheets integration not available: {e}")

# -------------------- RUN SCRAPING PROCESS (sync + async safe) --------------------

# Bounded log helper (prevents memory blowups)
MAX_LOG_LINES = 400
def log(msg: str):
    ts = time.strftime("%H:%M:%S")
    logs = st.session_state.get("logs", [])
    logs.append(f"{ts} - {msg}")
    if len(logs) > MAX_LOG_LINES:
        logs = logs[-MAX_LOG_LINES:]
    st.session_state["logs"] = logs

def run_scraper_safe(query: str, deep: bool, limits: dict):
    """
    Calls scrape_depop whether it's sync or async.
    Works even if a loop is already running (e.g., some Streamlit setups).
    """
    result = scrape_depop(query, deep=deep, limits=limits)
    if asyncio.iscoroutine(result):
        try:
            return asyncio.run(result)
        except RuntimeError:
            # If there's already a running event loop, use a dedicated one.
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(result)
            finally:
                loop.close()
    return result

if st.session_state.get("run"):
    st.session_state["logs"] = []  # reset each run

    # Prepare configuration
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

    rows: List[Dict] = []  # always define

    if scrape_depop is None:
        log("Scraper module not available - generating sample data")
        rows = [{
            "platform": "Depop",
            "brand": "Supreme",
            "item_name": f"{st.session_state.query} (sample)",
            "price": "$199",
            "size": "L",
            "condition": "Good condition",
            "link": f"https://www.depop.com/search/?q={st.session_state.query.replace(' ','%20')}",
        }]
    else:
        with st.spinner(f"🔍 Scraping Depop for '{st.session_state.query}'..."):
            log(f"Starting scrape for '{st.session_state.query}' (max {MAX_ITEMS} items, deep fetch: {st.session_state.deep})")
            t0 = time.time()
            try:
                result = run_scraper_safe(
                    st.session_state.query,
                    deep=st.session_state.deep,
                    limits=limits,
                )
                rows = result if isinstance(result, list) else []
                if not rows:
                    log("⚠️ Scraper returned no rows (None or unexpected type).")
            except Exception as e:
                rows = []
                log(f"❌ Scraping failed: {e}")
                st.error(f"Scraping failed: {e}")
            dur = time.time() - t0
            log(f"✅ Scraping completed in {dur:.1f}s - found {len(rows)} items")

    # Save to Google Sheets
    if gc and rows:
        with st.spinner("💾 Saving to Google Sheets..."):
            try:
                import gspread
                headers = ["Platform", "Brand", "Item Name", "Price", "Size", "Condition", "Link"]

                # Open or create spreadsheet
                try:
                    sheet = gc.open(SHEET_NAME)
                except gspread.SpreadsheetNotFound:
                    sheet = gc.create(SHEET_NAME)
                    log(f"Created new spreadsheet: {SHEET_NAME}")

                # Create or get worksheet
                tab_title = st.session_state.query[:99] or "Results"
                try:
                    worksheet = sheet.worksheet(tab_title)
                except gspread.WorksheetNotFound:
                    worksheet = sheet.add_worksheet(title=tab_title, rows="5000", cols=str(len(headers)))
                    worksheet.append_row(headers)
                    log(f"Created new worksheet: {tab_title}")

                # Clear sheet if requested
                if RESET_SHEET or not worksheet.get_all_values():
                    worksheet.clear()
                    worksheet.append_row(headers)
                    log("Cleared worksheet and added headers")

                # Prepare and batch insert data
                data_rows = [[
                    row.get("platform", "Depop"),
                    row.get("brand", ""),
                    row.get("item_name", ""),
                    row.get("price", ""),
                    row.get("size", ""),
                    row.get("condition", ""),
                    row.get("link", ""),
                ] for row in rows]

                BATCH_SIZE = 300
                total_batches = max(1, (len(data_rows) + BATCH_SIZE - 1) // BATCH_SIZE)
                for i in range(0, len(data_rows), BATCH_SIZE):
                    batch = data_rows[i:i + BATCH_SIZE]
                    worksheet.append_rows(batch, value_input_option="RAW")
                    log(f"Uploaded batch {i // BATCH_SIZE + 1}/{total_batches}")

                st.success(f"✅ Successfully saved {len(rows)} items to **{SHEET_NAME} / {tab_title}**")
                log(f"✅ Data saved to Google Sheets: {SHEET_NAME} / {tab_title}")

            except Exception as e:
                st.warning(f"⚠️ Could not save to Google Sheets: {e}")
                log(f"❌ Google Sheets save failed: {e}")

    # Display results
    if rows:
        render_results(rows, SHEET_NAME)
    else:
        st.info("No data to display. Try adjusting your search terms or settings.")
