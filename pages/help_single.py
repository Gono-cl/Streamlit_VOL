import streamlit as st

st.title("ℹ️ Help: Single Objective Optimization")
st.markdown("---")

st.markdown("""
## 🧪 What This Module Does

This module allows you to:
- Optimize a single response (e.g., yield, conversion) using Bayesian optimization
- Run in **simulation**, **hybrid**, or **real hardware** mode
- Save and resume optimization runs automatically

## 🛠 How to Use It

1. Define your experimental variables.
2. Set the optimization parameters (number of iterations, response to optimize).
3. Choose the mode (simulation/hybrid/off).
4. Start the experiment — results are plotted and saved live!

## 💡 Tips
- Use the **Stop** button if something goes wrong.
- Resume your experiment anytime from saved runs in the sidebar.
- Results and metadata are saved following FAIR principles.

## 🔁 Related Modules
- [Multi-objective Optimization](#)
- [Design of Experiments](#)
""")
