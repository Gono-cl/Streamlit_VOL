import streamlit as st

# App Title
st.markdown("<h1 style='color:#4CAF50; font-size: 42px;'>🧪 VOL - Virtual Optimization Lab</h1>", unsafe_allow_html=True)
st.subheader("A Modular Platform for Automated and Intelligent Experimentation")

# Horizontal line
st.markdown("---")

# Description
st.markdown("""
Welcome to **VOL (Virtual Optimization Lab)** — your intelligent assistant for chemical and process experimentation.
VOL integrates automation, machine learning, and interactive design to help you explore experimental space faster and smarter.

#### With VOL, you can:
- 🚀 Run **Single-Objective Bayesian Optimization** (Real, Hybrid, or Simulated)
- ⚖️ Perform **Multi-Objective Bayesian Optimization**
- 🎯 Use **Design of Experiments (DoE)** to structure offline campaigns
- 🔌 Connect to **real hardware systems via OPC** for live control and data acquisition
- 🧠 Perform both **automated** and **manual optimization campaigns** 
- 🔁 **Stop/Resume** any campaign — and even recover from hardware failures
- 💾 **Save and Reload** optimization runs (pick up where you left off!)
- 🗂️ Store experiment results in a **structured database** following the **FAIR principles**
- 🧠 Soon: Use **Previous Campaigns as Starting Points** (Active Learning)
""")

# Info box
st.info("“Empowering researchers to rapidly explore and optimize experimental space through automation and intelligent design.”")

# Spacer
st.markdown("")

# Layout: Two columns
col1, col2 = st.columns([1, 2])

with col1:
    st.image("assets/image.png", use_container_width=True)

with col2:
    st.markdown("### How to Get Started:")
    st.markdown("""
    1. Select a module from the **sidebar** (left).
    2. Define your experiment variables and objectives.
    3. Start an optimization or DoE campaign.
    4. Monitor live results — all data is automatically saved and visualized.
    
    ---
    """)
    st.success("🎯 Ready to experiment? Choose a module from the sidebar!")



