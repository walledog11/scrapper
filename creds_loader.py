# creds_loader.py
import os, json
import gspread

# Streamlit is optional here (only used for the badge)
try:
    import streamlit as st
except Exception:  # running outside Streamlit
    st = None

def _has_secrets():
    """Return (has_table, has_json, is_cloud)."""
    is_cloud = bool(os.environ.get("STREAMLIT_RUNTIME"))
    has_table = False
    has_json = False
    if st is not None:
        try:
            has_table = "google_service_account" in st.secrets
            has_json  = "GOOGLE_SERVICE_ACCOUNT" in st.secrets
        except Exception:
            pass
    return has_table, has_json, is_cloud

def authorize_gspread(prefer_local: bool = False) -> gspread.Client:
    """
    Cloud: prefer Streamlit Secrets ([google_service_account] or GOOGLE_SERVICE_ACCOUNT).
    Local: if prefer_local=True, use credentials.json in project root.
    Returns a gspread.Client or raises RuntimeError.
    """
    has_table, has_json, is_cloud = _has_secrets()

    # Cloud-first when not preferring local
    if not prefer_local and has_table and st is not None:
        source = "[google_service_account] (TOML table)"
        creds_dict = dict(st.secrets["google_service_account"])
        client = gspread.service_account_from_dict(creds_dict)
        _render_badge(ok=True, source=source)
        return client

    if not prefer_local and has_json and st is not None:
        source = "GOOGLE_SERVICE_ACCOUNT (JSON string)"
        creds_dict = json.loads(st.secrets["GOOGLE_SERVICE_ACCOUNT"])
        client = gspread.service_account_from_dict(creds_dict)
        _render_badge(ok=True, source=source)
        return client

    # Local fallback
    if os.path.exists("credentials.json"):
        source = "credentials.json (local file)"
        client = gspread.service_account(filename="credentials.json")
        _render_badge(ok=True, source=source)
        return client

    # Nothing worked
    _render_badge(
        ok=False,
        source="none",
        note=("Add [google_service_account] to Secrets (TOML with triple-quoted private_key) "
              "or place credentials.json next to app.py.")
    )
    raise RuntimeError(
        "No Google credentials found. Add [google_service_account] or GOOGLE_SERVICE_ACCOUNT in Secrets, "
        "or place credentials.json next to app.py"
    )

def _render_badge(ok: bool, source: str, note: str | None = None):
    """Show a small badge in the UI (no-op outside Streamlit)."""
    if st is None:
        return
    if ok:
        st.success(f"🔐 Secrets OK — using **{source}**")
    else:
        st.error("❌ No Google credentials detected")
        if note:
            st.caption(note)
