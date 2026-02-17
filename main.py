import importlib.util
import socket
from pathlib import Path

import streamlit as st

from core.utils import db_handler


# ===== Streamlit page configuration =====
st.set_page_config(
    page_title="VOL - Virtual Optimization Lab",
    page_icon="🧪",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ===== Hide default Streamlit UI =====
hide_streamlit_style = """
    <style>
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
    </style>
"""
st.markdown(hide_streamlit_style, unsafe_allow_html=True)

# ===== Initialize database =====
db_handler.init_db()

hostname = socket.gethostname()
local_ip = socket.gethostbyname(hostname)

# if local_ip.startswith("127."):
st.user = type("obj", (), {"is_logged_in": True, "name": "LocalDev", "email": "dev@local.com"})()

# ===== Google OAuth login =====
if not st.user.is_logged_in:
    col1, col2, col3 = st.columns([1, 1, 1])
    with col2:
        st.image("image.png", use_container_width=True)

    st.markdown(
        """
        <div style='text-align: center; margin-top: 30px;'>
            <h1>VirtualOptLab</h1>
            <p style='font-size: 20px;'>Sign in with Google to access your experiments.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    col1, col2, col3 = st.columns([3, 1, 3])
    with col2:
        st.button("Log in with Google", on_click=st.login)
    st.stop()

# ===== Sidebar: logout + user info =====
# if not local_ip.startswith("127."):
# st.sidebar.button("Log out", on_click=st.logout)
st.sidebar.write(f"User: {st.user.name}")
st.sidebar.write(f"Email: {st.user.email}")

# ===== Define app pages =====
PAGES = {
    "Home": "Home.py",
    "Autonomous Single Objective Optimization": "Single_Objective.py",
    "Autonomous Multi-Objective Optimization": "Multi_Objective.py",
    "DOE Executor (External Matrix)": "DoE_Executor.py",
    "Reproducibility Studio": "Reproducibility.py",
    "Running Protocol Library": "Running_Protocols.py",
    "Data Analysis & Visualization": "Data_Analysis.py",
    "Preview Saved Run": "preview_run.py",
    "Experiment DataBase": "experiment_database.py",
}

PAGE_ALIASES = {
    "home": "Home.py",
    "single_objective": "Single_Objective.py",
    "multi_objective": "Multi_Objective.py",
    "doe_executor": "DoE_Executor.py",
    "reproducibility": "Reproducibility.py",
    "running_protocols": "Running_Protocols.py",
    "data_analysis": "Data_Analysis.py",
    "preview_saved_run": "preview_run.py",
    "experiment_database": "experiment_database.py",
}
NAV_SELECTION_KEY = "_nav_selected_label"


def _label_for_path(page_path: str) -> str:
    for label, path in PAGES.items():
        if path == page_path:
            return label
    return list(PAGES.keys())[0]


def _resolve_selection(raw_selection) -> str:
    """
    Resolve page requests robustly.
    Supports exact labels, stable alias IDs, file paths, and keyword matching.
    """
    if raw_selection in PAGES:
        return str(raw_selection)

    raw = str(raw_selection or "").strip()
    if raw in PAGE_ALIASES:
        return _label_for_path(PAGE_ALIASES[raw])

    if raw in PAGES.values():
        return _label_for_path(raw)

    lowered = raw.lower()
    keyword_routes = [
        ("single objective", "Single_Objective.py"),
        ("multi-objective", "Multi_Objective.py"),
        ("multi objective", "Multi_Objective.py"),
        ("reproducibility", "Reproducibility.py"),
        ("doe", "DoE_Executor.py"),
        ("protocol", "Running_Protocols.py"),
        ("analysis", "Data_Analysis.py"),
        ("preview", "preview_run.py"),
        ("database", "experiment_database.py"),
        ("home", "Home.py"),
    ]
    for keyword, page_path in keyword_routes:
        if keyword in lowered:
            return _label_for_path(page_path)

    return list(PAGES.keys())[0]


# ===== Sidebar navigation =====
st.sidebar.image("assets/image.png", width=300)
st.sidebar.title("Navigation")

if NAV_SELECTION_KEY not in st.session_state or st.session_state.get(NAV_SELECTION_KEY) not in PAGES:
    st.session_state[NAV_SELECTION_KEY] = list(PAGES.keys())[0]

if "selected_page" in st.session_state:
    requested = _resolve_selection(st.session_state.pop("selected_page"))
    st.session_state[NAV_SELECTION_KEY] = requested

selection = st.sidebar.radio("Go to", list(PAGES.keys()), key=NAV_SELECTION_KEY)

selection = _resolve_selection(selection)


# ===== Load selected page =====
def load_page(page_path):
    spec = importlib.util.spec_from_file_location("page", Path(page_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)


load_page(PAGES[selection])
