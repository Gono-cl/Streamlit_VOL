import streamlit as st
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from skopt import gp_minimize
from skopt.utils import use_named_args

st.title("🎓 Bayesian Optimization Classroom")

st.markdown("""
This page introduces the key concepts of **Bayesian Optimization (BO)** through simple explanations and an example based on **reaction optimization in chemistry**.
""")

# --- Section 1: Introduction ---
st.header("📘 What is Bayesian Optimization?")

st.markdown("""
Bayesian Optimization is a method for optimizing expensive or unknown functions.
It builds a model (called a **surrogate**) of the objective function and uses an **acquisition function** to decide where to sample next.

BO is particularly useful when:
- Evaluations are expensive (e.g., chemical experiments)
- The function is unknown or noisy
- You want to minimize the number of trials

Key components:
- 🎯 **Objective Function**: the outcome you're trying to optimize (e.g. yield)
- 🧠 **Surrogate Model**: usually a Gaussian Process (GP) that models your data
- 🚦 **Acquisition Function**: decides where to sample next
""")

# --- Section 2: Visual Intuition ---
st.header("🧠 Visualizing the BO Process")

with st.expander("Step-by-step Explanation"):
    st.markdown("""
    1. Start with a few initial experiments (random temperature/time).
    2. Fit a Gaussian Process to the yield data.
    3. Use the acquisition function (e.g. Expected Improvement) to pick the next condition.
    4. Run the new experiment, update the model, and repeat.
    """)

# --- Section 3: Interactive Demo ---
st.header("⚗️ Reaction Yield Optimization (Simulated)")

st.markdown("""
We will optimize a simulated reaction yield depending on:
- **Temperature** (30°C to 110°C)
- **Time** (10 to 60 minutes)

The underlying (hidden) function has an optimum around 70°C and 30 minutes.
""")

# Define simulated chemical yield function
def chemical_yield(params):
    T, t = params
    return -(
        np.exp(-((T - 70)**2) / 100) *
        np.exp(-((t - 30)**2) / 200)
    )  # Negative for minimization

n_calls = st.slider("Number of BO Iterations", min_value=5, max_value=30, value=10)
n_initial_points = st.slider("Number of Initial Points", min_value=1, max_value=10, value=3)
acq_func = st.selectbox("Acquisition Function", options=["EI", "PI", "LCB"], index=0)

if st.button("🚀 Run Optimization"):
    result = gp_minimize(
        chemical_yield,
        dimensions=[(30.0, 110.0), (10.0, 60.0)],
        n_calls=n_calls,
        n_initial_points=n_initial_points,
        acq_func=acq_func,
        random_state=0
    )

    best_yield = -result.fun * 100  # convert to %
    st.success(f"Best yield: {best_yield:.2f}% at T = {result.x[0]:.1f}°C, t = {result.x[1]:.1f} min")

    # Custom convergence plot showing yield
    yields = [-y * 100 for y in result.func_vals]  # Convert all to % yield
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(range(1, len(yields) + 1), yields, marker='o')
    ax.set_xlabel("Iteration")
    ax.set_ylabel("Yield (%)")
    ax.set_title("Convergence Plot: Maximizing Yield")
    ax.grid(True)
    st.pyplot(fig, use_container_width = False )

    # Scatter plot of explored points
    T_vals = [x[0] for x in result.x_iters]
    t_vals = [x[1] for x in result.x_iters]
    z_vals = [-y * 100 for y in result.func_vals]

    fig2, ax2 = plt.subplots(figsize=(6, 4))
    scatter = ax2.scatter(T_vals, t_vals, c=z_vals, cmap="viridis", s=80)
    plt.colorbar(scatter, ax=ax2, label="Yield (%)")
    ax2.set_xlabel("Temperature (°C)")
    ax2.set_ylabel("Time (min)")
    ax2.set_title("Explored Conditions and Yields")
    st.pyplot(fig2, use_container_width = False)

# --- Section 4: Glossary ---
st.header("📚 BO Glossary")

with st.expander("Key Terms"):
    st.markdown("""
    - **Surrogate Model**: a model that approximates the true function (usually a GP)
    - **Acquisition Function**: selects the next point to evaluate
    - **Exploration**: sampling where the model is uncertain
    - **Exploitation**: sampling where the model predicts the best result
    - **EI**: Expected Improvement — a common acquisition function
    - **PI**: Probability of Improvement — favors probable gains
    - **LCB**: Lower Confidence Bound — balances mean and uncertainty
    - **n_init**: number of initial (random) samples
    - **Convergence**: when new samples no longer significantly improve results
    """)