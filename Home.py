import streamlit as st

st.markdown(
    "<h1 style='color:#4CAF50; font-size: 42px;'>VOL - Virtual Optimization Lab</h1>",
    unsafe_allow_html=True,
)
st.subheader("A modular platform for autonomous experimentation and optimization")

st.markdown("---")

st.markdown("""
Welcome to **VOL (Virtual Optimization Lab)**.
VOL combines optimization, hardware execution, and experiment tracking in one Streamlit app.

#### With VOL, you can:
- Run **Single-Objective Bayesian Optimization** (real, hybrid, or full simulation)
- Run **Multi-Objective Optimization** with objective direction control
- Execute external DOE matrices with **DOE Executor**
- Select a **Running Protocol File** per reaction from the GUI
- Browse protocol files and preview protocol schemes in **Running Protocol Library**
- Connect to hardware through **OPC** for live control and data acquisition
- Stop, resume, save, and reload optimization runs
- Analyze results in **Data Analysis & Visualization**
- Persist experiment records in the **Experiment Database**
""")

st.info("Create and version reaction protocols as `.py` files in `running_protocols/` without changing core execution logic.")

st.markdown("")

col1, col2 = st.columns([1, 2])

with col1:
    st.image("assets/image.png", use_container_width=True)

with col2:
    st.markdown("### How to Get Started:")
    st.markdown("""
    1. Open **Running Protocol Library** and select the protocol file for your reaction.
    2. Go to **Single Objective**, **Multi-Objective**, or **DOE Executor**.
    3. Set variables/objectives and run configuration.
    4. Start execution and monitor results.
    5. Review outcomes in **Data Analysis** and **Experiment Database**.

    ---
    """)
    st.success("Ready to experiment? Choose a module from the sidebar.")
