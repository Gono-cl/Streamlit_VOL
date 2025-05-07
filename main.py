import streamlit as st
from pathlib import Path
import importlib.util
from core.utils import db_handler

# Initialize your database
db_handler.init_db()

# ✅ Set up the Streamlit page
st.set_page_config(
    page_title="VOL - Virtual Optimization Lab",
    page_icon="🧪",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ✅ Hide Streamlit default UI elements
hide_streamlit_style = """
    <style>
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        header {visibility: hidden;}
    </style>
"""
st.markdown(hide_streamlit_style, unsafe_allow_html=True)

# ✅ Define all app pages
PAGES = {
    "🏠 Home": "home.py",
    "🎯 Autonomous Single Objective Optimization": "single_objective.py",
    "📊 Autonomous Multi-Objective Optimization": "multi_objective.py",
    "🧰 Manual Optimization": "manual_experiments.py",
    "🧪 Design of Experiments": "DoE.py",
    "📚 Experiment DataBase": "experiment_database.py",
    "🔍 Preview Saved Run": "preview_run.py"
}

# ✅ Navigation Sidebar
st.sidebar.image("assets/image.png", width=300)
st.sidebar.title("📁 Navigation")

# ✅ Check if a page was requested programmatically (e.g., from preview_run.py)
if "selected_page" in st.session_state:
    selection = st.session_state.selected_page
    del st.session_state.selected_page  # Clean up after redirect
else:
    selection = st.sidebar.radio("Go to", list(PAGES.keys()))

# ✅ Load and run the selected page
def load_page(page_path):
    spec = importlib.util.spec_from_file_location("page", Path(page_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

load_page(PAGES[selection])

