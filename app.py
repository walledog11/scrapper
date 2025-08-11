# --- SAFE debug + credentials loader (works local + cloud) ---
import os, json, subprocess, sys
import streamlit as st
import gspread
from oauth2client.service_account import ServiceAccountCredentials

SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

def load_google_credentials():
    """
    Cloud: use st.secrets['GOOGLE_SERVICE_ACCOUNT'] if present.
    Local: fall back to credentials.json in project root.
    """
    sj = st.secrets.get("GOOGLE_SERVICE_ACCOUNT")  # <- safe get (won't throw)
    if sj:
        try:
            creds_dict = json.loads(sj)
            st.info("🔐 Using Google creds from Streamlit Secrets.")
            return ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, SCOPE)
        except json.JSONDecodeError as e:
            st.error(f"❌ Secret GOOGLE_SERVICE_ACCOUNT is not valid JSON: {e}")
            st.stop()

    # Local fallback
    if os.path.exists("credentials.json"):
        st.info("📄 Using local credentials.json (dev mode).")
        return ServiceAccountCredentials.from_json_keyfile_name("credentials.json", SCOPE)

    st.error("❌ No Google credentials: add GOOGLE_SERVICE_ACCOUNT in Secrets or place credentials.json locally.")
    st.stop()

# ----------------- Optional on-page health panel -----------------
st.set_page_config(page_title="Depop Scraper", page_icon="🧢", layout="wide")
st.title("🧢 Depop Scraper")

with st.expander("🔍 Health check", expanded=True):
    # Secrets / local file visibility
    if st.secrets.get("GOOGLE_SERVICE_ACCOUNT"):
        try:
            json.loads(st.secrets["GOOGLE_SERVICE_ACCOUNT"])
            st.success("✅ Secret present and valid JSON.")
        except json.JSONDecodeError as e:
            st.error(f"❌ Secret invalid JSON: {e}")
    else:
        st.info("ℹ️ No secret detected. Expecting local credentials.json.")

    # Imports OK (you’re here, so these worked)
    st.success("✅ gspread / oauth2client imported")

    # Ensure Chromium (best-effort, won't crash)
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium", "--with-deps"],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        st.success("✅ Playwright Chromium present/installed")
    except Exception as e:
        st.warning(f"⚠️ Could not ensure Chromium: {e}")

# Authorize Sheets client using flexible loader
creds = load_google_credentials()
gc = gspread.authorize(creds)
