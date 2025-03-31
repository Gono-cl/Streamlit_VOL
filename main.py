import streamlit as st
from pathlib import Path
import importlib.util

# ✅ Must be the first Streamlit command
st.set_page_config(
    page_title="VOL - Virtual Optimization Lab",
    page_icon="🧪",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Hide the top-left Streamlit menu and footer
hide_streamlit_style = """
    <style>
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        header {visibility: hidden;}
    </style>
"""
st.markdown(hide_streamlit_style, unsafe_allow_html=True)

# Define page options
PAGES = {
    "🏠 Home": "home.py",
    "🎯 Single Objective Optimization": "single_objective.py",
    "📊 Multi-Objective Optimization": "multi_objective.py",
    "🧪 Design of Experiments": "DoE.py"
}

# Sidebar for navigation
st.sidebar.image("assets/image.png", width=300)
st.sidebar.title("📁 Navigation")
selection = st.sidebar.radio("Go to", list(PAGES.keys()))

# Dynamically import and run the selected page
def load_page(page_path):
    spec = importlib.util.spec_from_file_location("page", Path(page_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

# Load the selected page
load_page(PAGES[selection])

