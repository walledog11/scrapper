# creds_loader.py
import os, json
import streamlit as st
import gspread

def authorize_gspread(prefer_local: bool = False):
    """
    Cloud: prefer Streamlit Secrets ([google_service_account] or GOOGLE_SERVICE_ACCOUNT).
    Local: if prefer_local=True, use credentials.json next to app.py.
    Returns: gspread.Client
    """
    # Cloud-first: TOML table
    if not prefer_local and "google_service_account" in st.secrets:
        creds_dict = dict(st.secrets["google_service_account"])
        return gspread.service_account_from_dict(creds_dict)

    # Cloud alt: JSON string
    if not prefer_local and "GOOGLE_SERVICE_ACCOUNT" in st.secrets:
        creds_dict = json.loads(st.secrets["GOOGLE_SERVICE_ACCOUNT"])
        return gspread.service_account_from_dict(creds_dict)

    # Local dev fallback
    if os.path.exists("credentials.json"):
        return gspread.service_account(filename="credentials.json")

    raise RuntimeError(
        "No Google credentials found. Add [google_service_account] or GOOGLE_SERVICE_ACCOUNT in Secrets, "
        "or place credentials.json next to app.py"
    )
