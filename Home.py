import streamlit as st


# App Title
st.markdown("<h1 style='color:#4CAF50; font-size: 42px;'>🧪 VOL - Virtual Optimization Lab</h1>", unsafe_allow_html=True)
st.subheader("A Modular Platform for Automated Experimentation")

# Horizontal line
st.markdown("---")

# Description
st.markdown("""
Welcome to the **Virtual Optimization Lab (VOL)**! This platform is designed to streamline and automate experimental workflows in chemical and process optimization.

#### With VOL, you can:
- 🚀 Run **Single-Objective Optimization** using Bayesian methods
- ⚖️ Conduct **Multi-Objective Optimization** with desirability functions
- 🧪 Perform structured **Design of Experiments (DoE)**
""")

# Add a nice info box or a motivational quote
st.info("“Empowering researchers to rapidly explore and optimize experimental space through automation and intelligent design.”")



# Spacer
st.markdown("")

# Layout: Two columns
col1, col2 = st.columns([1, 2])

with col1:
    st.image("assets/image.png",use_container_width=True)

with col2:
    st.markdown("### How to Get Started:")
    st.markdown("""
    1. Select a module from the **Navigation** sidebar on the left.
    2. Configure your experiment variables or objectives.
    3. Launch the optimization or design process.
    4. View results live — plotted and stored automatically!
    
    ---
    """)
    st.success("Ready to experiment? Choose a module from the sidebar!")


