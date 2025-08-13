import os, io, csv, time
from typing import List, Dict
import streamlit as st
from pandas import DataFrame

# --- Page setup
st.set_page_config(page_title="Depop Scraper", page_icon="🧢", layout="wide")

# --- Custom CSS for theme support, alignment, and slider containment
st.markdown("""
    <style>
        /* Base overrides for dark/light mode */
        [data-theme="dark"] body {
            background-color: #1e293b !important;
            color: #f1f5f9 !important;
        }
        [data-theme="light"] body {
            background-color: #f7f9fc !important;
            color: #1e293b !important;
        }
        /* Input and slider colors */
        [data-theme="dark"] .stTextInput > div > input, [data-theme="dark"] .stNumberInput > div > input, [data-theme="dark"] .stSlider > div {
            background-color: #334155 !important;
            color: #f1f5f9 !important;
            border: 1px solid #475569 !important;
        }
        [data-theme="dark"] .stToggle > label {
            color: #f1f5f9 !important;
        }
        [data-theme="dark"] .stInfo {
            background-color: #2d3748 !important;
            color: #f1f5f9 !important;
        }
        /* Sidebar slider overflow fix */
        [data-testid="stSidebar"] .stSlider {
            max-width: 100% !important;
            overflow: hidden !important;
            padding-right: 0 !important;
        }
        [data-testid="stSidebar"] {
            overflow: hidden !important;
        }
        /* Other styles from previous version */
        .stApp {
            font-family: 'Inter', sans-serif;
        }
        .main-header {
            font-size: 28px;
            font-weight: 600;
        }
        .subheader {
            font-size: 14px;
            color: #64748b;
            margin-bottom: 16px;
        }
        .status-badge {
            font-size: 12px;
            padding: 6px 12px;
            border-radius: 12px;
            color: white;
        }
        .status-ok { background-color: #22c55e; }
        .status-local { background-color: #f59e0b; }
        .status-error { background-color: #ef4444; }
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
        [data-theme="dark"] .main-header, [data-theme="dark"] .subheader {
            color: #f1f5f9 !important;
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

# Rest of the code remains the same as your previous version. Replace the CSS block and keep the rest.

# --- (The full code is truncated for brevity; replace the CSS in your app.py with the new one above)