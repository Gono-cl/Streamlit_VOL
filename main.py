import streamlit as st
from pathlib import Path
import importlib.util
from core.utils import db_handler

# ===== Streamlit page configuration =====
st.set_page_config(
    page_title="VOL - Virtual Optimization Lab",
    page_icon="🧪",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ===== Hide default Streamlit UI =====
hide_streamlit_style = """
    <style>
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        header {visibility: hidden;}
    </style>
"""
st.markdown(hide_streamlit_style, unsafe_allow_html=True)

# ===== Initialize database =====
db_handler.init_db()

# ===== Google OAuth login =====
if not st.user.is_logged_in:
    # Center the image using columns
    col1, col2, col3 = st.columns([1,1,1])
    with col2:
        st.image("image.png", use_container_width=True)

    st.markdown(
        """
        <div style='text-align: center; margin-top: 30px;'>
            <h1>🔒 VirtualOptLab</h1>
            <p style='font-size: 20px;'>Sign in with Google to access your experiments.</p>
        </div>
        """,
        unsafe_allow_html=True
    )

    col1, col2, col3 = st.columns([3,1,3])
    with col2:
        st.button("🔐 Log in with Google", on_click=st.login)
    st.stop()

# ===== Sidebar: logout + user info =====
st.sidebar.button("🚪 Log out", on_click=st.logout)
st.sidebar.write(f"👤 {st.user.name}")
st.sidebar.write(f"✉️ {st.user.email}")

# ===== Define app pages =====
PAGES = {
    "🏠 Home": "Home.py",
    "🎯 Autonomous Single Objective Optimization": "Single_Objective.py",
    "📊 Autonomous Multi-Objective Optimization": "Multi_Objective.py",
    "🧰 Manual Optimization": "manual_experiments.py",
    "🔄 Custom Workflow": "custom_workflow.py",
    "🧪 Design of Experiments": "DoE.py",
    "📚 Experiment DataBase": "experiment_database.py",
    "🔍 Preview Saved Run": "preview_run.py",
    "🎓 Bayesian Optimization Classroom": "BO_classroom.py",
    "❓ FAQ – Help & Guidance": "faq.py"
}

# ===== Sidebar navigation =====
st.sidebar.image("assets/image.png", width=300)
st.sidebar.title("📁 Navigation")

if "selected_page" in st.session_state:
    selection = st.session_state.selected_page
    del st.session_state.selected_page
else:
    selection = st.sidebar.radio("Go to", list(PAGES.keys()))

# ===== Load selected page =====
def load_page(page_path):
    spec = importlib.util.spec_from_file_location("page", Path(page_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

load_page(PAGES[selection])

st.image("https://your-logo-url.com/logo.png", width=150)


