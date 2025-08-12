# creds_loader.py
import os, json
import gspread

try:
    import streamlit as st  # available on Cloud and when running `streamlit run`
except Exception:
    st = None  # allow import without Streamlit (e.g., plain scripts/tests)



def authorize_gspread(prefer_local: bool = False) -> gspread.Client:
    """
    Priority:
      - In Cloud: [google_service_account] table → GOOGLE_SERVICE_ACCOUNT JSON string → (no local)
      - Locally (prefer_local=True): credentials.json → [google_service_account] → GOOGLE_SERVICE_ACCOUNT
      - Locally (prefer_local=False): [google_service_account] → GOOGLE_SERVICE_ACCOUNT → credentials.json
    Shows a badge indicating which source was used (when Streamlit is available).
    """
    IS_CLOUD = bool(os.environ.get("STREAMLIT_RUNTIME"))

    # Cloud forces secrets
    if IS_CLOUD:
        prefer_local = False

    has_table = has_json = False
    if st is not None:
        try:
            has_table = "google_service_account" in st.secrets
            has_json  = "GOOGLE_SERVICE_ACCOUNT" in st.secrets
        except Exception:
            pass

    order = ["table", "json", "local"]
    if prefer_local:
        order = ["local", "table", "json"]

    # 1) TOML table: [google_service_account]
    if "table" in order and has_table:
        try:
            creds_dict = dict(st.secrets["google_service_account"])
            client = gspread.service_account_from_dict(creds_dict)
            _badge(True, "[google_service_account] (TOML table)")
            return client
        except Exception as e:
            if st: st.warning(f"Credential source 'table' failed: {e}")

    # 2) JSON string: GOOGLE_SERVICE_ACCOUNT
    if "json" in order and has_json:
        try:
            creds_dict = json.loads(st.secrets["GOOGLE_SERVICE_ACCOUNT"])
            client = gspread.service_account_from_dict(creds_dict)
            _badge(True, "GOOGLE_SERVICE_ACCOUNT (JSON string)")
            return client
        except Exception as e:
            if st: st.warning(f"Credential source 'json' failed: {e}")

    # 3) Local file: credentials.json
    if "local" in order and os.path.exists("credentials.json"):
        try:
            client = gspread.service_account(filename="credentials.json")
            _badge(True, "credentials.json (local file)")
            return client
        except Exception as e:
            if st: st.warning(f"Credential source 'local' failed: {e}")

    _badge(False, "none", "Add [google_service_account] in Secrets (triple-quoted private_key) "
                          "or set GOOGLE_SERVICE_ACCOUNT as a JSON string, or place credentials.json next to app.py.")
    raise RuntimeError("No Google credentials found. Configure Secrets or add credentials.json.")
