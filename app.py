import os, io, csv, time
from typing import List, Dict
import streamlit as st

# --- Page setup
st.set_page_config(page_title="Depop Scraper", page_icon="🧢", layout="wide")

# --- First-time help text
FIRST_TIME_HELP = """
**First time setup**
1) In Streamlit Cloud → **Settings → Secrets**, add your Google service account under  
   **`[google_service_account]`** with a triple-quoted `private_key`.
2) Share your target Google Sheet with the service account email (Editor).
3) That’s it — run a search and the app will write to your sheet.
"""

# --- Helpers to render UI sections
def render_header():
    left, right = st.columns([0.85, 0.15], vertical_alignment="center")
    with left:
        st.markdown("### 🧢 Depop Scraper")
        st.caption("Search Depop, deep-scrape size & condition, and export to Google Sheets.")
    with right:
        if st.session_state.get("secrets_ok"):
            st.markdown("🟢 Secrets OK")
        elif st.session_state.get("local_creds_ok"):
            st.markdown("🟡 Local creds")
        else:
            st.markdown("🔴 No creds")

def render_controls():
    with st.container():
        c1, c2, c3 = st.columns([4,1,1])
        with c1:
            st.session_state.query = st.text_input(
                "Search term",
                value=st.session_state.get("query", "Supreme Box Logo"),
                placeholder="e.g. palace hoodie, arcteryx alpha..."
            )
        with c2:
            st.session_state.deep = st.toggle(
                "Deep fetch",
                value=st.session_state.get("deep", True),
                help="Visit item pages to extract Size & Condition."
            )
        with c3:
            st.session_state.run = st.button("🚀 Run scrape", use_container_width=True, type="primary")

def render_health():
    ft, health = st.tabs(["🧭 First time?", "🩺 Health check"])
    with ft:
        st.markdown(FIRST_TIME_HELP)
    with health:
        st.info("Playwright: auto-installed at runtime on the cloud.")
        st.info("Google Sheets: Service Account from secrets or local credentials.json.")
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
    k1, k2, k3 = st.columns(3)
    with k1: st.metric("Items scraped", len(rows))
    with k2: st.metric("Saved to Sheets", sheet_name)
    with k3:
        brands = len({r.get('brand','').strip() for r in rows if r.get('brand')})
        st.metric("Brand coverage", brands)

    tabs = st.tabs(["📄 Table", "📥 Download CSV", "🪵 Logs"])
    with tabs[0]:
        if rows:
            from pandas import DataFrame
            df = DataFrame(rows, columns=["platform","brand","item_name","price","size","condition","link"])
            st.dataframe(df, use_container_width=True)
        else:
            st.warning("No rows to display yet.")

    with tabs[1]:
        if rows:
            output = io.StringIO()
            w = csv.writer(output)
            w.writerow(["Platform","Brand","Item Name","Price","Size","Condition","Link"])
            for r in rows:
                w.writerow([
                    r.get("platform",""), r.get("brand",""), r.get("item_name",""),
                    r.get("price",""), r.get("size",""), r.get("condition",""), r.get("link","")
                ])
            st.download_button(
                "Download CSV",
                output.getvalue().encode("utf-8"),
                file_name=f"depop_{st.session_state.query.replace(' ','_')}.csv",
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
    st.header("Settings")
    IS_CLOUD = bool(os.environ.get("STREAMLIT_RUNTIME"))
    prefer_local = st.toggle("Prefer local credentials.json (debug)", value=not IS_CLOUD)
    SHEET_NAME = st.text_input("Google Sheet name", value="depop_scraper", help="Spreadsheet (doc) name")
    RESET_SHEET = st.toggle("Reset tab headers on write", value=False)

    st.subheader("Limits")
    MAX_ITEMS = st.number_input("Max items (safety cap)", min_value=100, max_value=20000, value=3000, step=100)
    MAX_DURATION_S = st.number_input("Max duration (seconds)", min_value=60, max_value=3600, value=900, step=30)

    st.subheader("Deep fetch")
    DEEP_FETCH_MAX = st.number_input("Max deep-fetched items", min_value=50, max_value=5000, value=1000, step=50)
    DEEP_FETCH_CONCURRENCY = st.slider("Deep fetch concurrency", 1, 6, 3)
    DEEP_FETCH_DELAY_MIN, DEEP_FETCH_DELAY_MAX = st.slider("Per detail page delay (ms)", 200, 4000, (800, 1600))

    st.subheader("Advanced scrolling")
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
