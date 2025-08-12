# app.py — streamlined header using creds_loader

import os, sys, subprocess, datetime as _dt, streamlit as st

print("[HEARTBEAT]", _dt.datetime.utcnow().isoformat(), "UTC app starting")
IS_CLOUD = bool(os.environ.get("STREAMLIT_RUNTIME"))

st.set_page_config(page_title="Depop Scraper", page_icon="🧢", layout="wide")
st.title("🧢 Depop Scraper")

# ---------- Sidebar ----------
with st.sidebar:
    st.header("⚙️ Settings")
    prefer_local = st.toggle(
        "Prefer local credentials.json (dev)",
        value=not IS_CLOUD,
        help="Local dev can use credentials.json; Cloud forces Secrets."
    )
    if IS_CLOUD:
        prefer_local = False
        st.caption("☁️ Cloud mode: using **Secrets** (credentials.json ignored).")

    sheet_name = st.text_input("Google Sheet name", value="depop_scraper")

    st.subheader("Limits")
    max_items = st.number_input("Max items (cap)", min_value=50, max_value=20000, value=1000, step=50)
    max_seconds = st.number_input("Max duration (seconds)", min_value=60, max_value=3600, value=900, step=30)
    deep_fetch = st.toggle("Deep fetch product pages (Size/Condition)", value=True)

# ---------- Health check (optional) ----------
with st.expander("🔍 Health check", expanded=True):
    st.write("CWD:", os.getcwd())
    try: st.write("Files:", os.listdir())
    except Exception as e: st.write("Could not list files:", e)
    try: st.write("Secrets present? [google_service_account]:", "google_service_account" in st.secrets)
    except Exception as e: st.write("st.secrets not available:", e)
    # Ensure Playwright Chromium (best-effort; won’t crash if fails)
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium", "--with-deps"],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        st.success("✅ Playwright Chromium present/installed (best-effort)")
    except Exception as e:
        st.info(f"(Optional) Could not ensure Chromium: {e}")

# ---------- Main controls ----------
search_term = st.text_input("Search term", value="Supreme Box Logo")
run = st.button("Run scrape 🚀", type="primary")

# ---------- Google Sheets auth (badge shows which source was used) ----------
from creds_loader import authorize_gspread
gc = None
try:
    gc = authorize_gspread(prefer_local=prefer_local)
    try:
        st.caption("Service account: " + getattr(gc.auth, "service_account_email", "unknown"))
    except Exception:
        pass
except Exception as e:
    st.warning(f"Sheets auth not ready (UI continues): {e}")
    if IS_CLOUD:
        st.info("Cloud tip: ensure **[google_service_account]** is set in Settings → Secrets (private_key triple-quoted).")

# ---------- Your existing scrape handler below ----------
# Example:
# if run:
#     from depop_scraper_lib import scrape_depop
#     limits = dict(MAX_ITEMS=int(max_items), MAX_DURATION_S=int(max_seconds))
#     rows = scrape_depop(search_term, deep=deep_fetch, limits=limits)
#     ... save to Sheets using gc ...
#     ... show st.dataframe ...

# ---------------- Run scrape ----------------
if run:
    st.info(f"Starting scrape for **{search_term}** (max {max_items}, deep={deep_fetch})")
    try:
        from depop_scraper_lib import scrape_depop  # your scraper library
    except Exception as e:
        st.error(f"Could not import scraper module: {e}")
    else:
        try:
            limits = dict(MAX_ITEMS=int(max_items), MAX_DURATION_S=int(max_seconds))
            rows = scrape_depop(search_term, deep=deep_fetch, limits=limits)
            st.success(f"Scraped {len(rows)} items.")

            # Save to Google Sheets if authorized
            if gc:
                import gspread
                try:
                    try:
                        sh = gc.open(sheet_name)
                    except gspread.SpreadsheetNotFound:
                        sh = gc.create(sheet_name)
                    ws = sh.sheet1
                    if not ws.get_all_values():
                        ws.append_row(["Platform","Brand","Item Name","Price","Size","Condition","Link"])
                    for r in rows:
                        ws.append_row([
                            r.get("platform","Depop"),
                            r.get("brand",""),
                            r.get("item_name",""),
                            r.get("price",""),
                            r.get("size",""),
                            r.get("condition",""),
                            r.get("link",""),
                        ])
                    st.success(f"✅ Saved {len(rows)} rows to **{sheet_name}**")
                except Exception as e:
                    st.warning(f"Could not write to Google Sheets: {e}")
            else:
                st.info("Skipped saving to Sheets (not authorized).")

            if rows:
                st.dataframe(rows[:200])
        except Exception as e:
            st.error("Scrape failed:")
            st.exception(e)
