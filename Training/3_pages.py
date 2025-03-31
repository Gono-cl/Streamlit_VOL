import streamlit as st
import pandas as pd
import numpy as np

# --- PAGE CONFIG ---
st.set_page_config(page_title="VOL - Virtual Optimization Laboratory", layout="wide")

# --- APP NAME & HEADER ---
st.markdown("""
    <h1 style='text-align: center; color: #4F8BF9;'>
        🔬 VOL: Virtual Optimization Laboratory
    </h1>
    <p style='text-align: center; font-size: 18px;'>
        An interactive platform for experiment design and optimization powered by Bayesian algorithms.
    </p>
""", unsafe_allow_html=True)

# --- SIDEBAR ---
st.sidebar.image('/Users/gon41849/Documents/Modular_code_repo/Streamlit_VOL/image.png', width=600)
st.sidebar.markdown("### 🧭 Navigation")



st.sidebar.markdown("### Autonomous Experiments")

# Define options
options = {
    "🔹 Single-Objective Optimization": "single",
    "🔸 Multi-Objective Optimization": "multi",
    "📐 Design of Experiments": "doe"
}



# Sidebar selection
selected_label = st.sidebar.radio("Choose a mode:", list(options.keys()))
selected_page = options[selected_label]

st.sidebar.markdown("### Off-Line Experiments")
options = {
    "🔹 Single-Objective Optimization 2": "single",
    "🔸 Multi-Objective Optimization 2": "multi",
    "📐 Design of Experiments 2": "doe"
}

# --- MAIN CONTENT ---
st.markdown("---")

if selected_page == "single":
    st.subheader("🔹 Single-Objective Optimization")
    st.write("Run a Bayesian Optimization with one target variable.")
    df = pd.DataFrame({'first column': [1, 2, 3, 4],'second column': [10, 20, 30, 40]})
    option = st.selectbox('Which number do you like best?',df['second column'])
    'You selected: ', option

elif selected_page == "multi":
    st.subheader("🔸 Multi-Objective Optimization")
    st.write("Optimize multiple objectives simultaneously using Pareto fronts.")
    dataframe = pd.DataFrame(
    np.random.randn(10, 20),
    columns=('col %d' % i for i in range(20)))
    st.dataframe(dataframe.style.highlight_max(axis=0))
    x = st.slider('x')  # 👈 this is a widget
    st.write(x, 'squared is', x * x)

elif selected_page == "doe":
    st.subheader("📐 Design of Experiments")
    st.write("Generate initial design points using DoE strategies like Latin Hypercube, Full Factorial, or Sobol sequences.")
    chart_data = pd.DataFrame(np.random.randn(20, 3),columns=['a', 'b', 'c'])
    st.line_chart(chart_data)



