import streamlit as st

st.title("❓ VOL Platform – Frequently Asked Questions (FAQ)")

faqs = {
    "What is the difference between manual and automated optimization modes in VOL?":
        "In manual mode, the user inputs results from experiments manually, often for offline or lab-scale work. In automated mode, VOL communicates directly with hardware and inline analytics to execute and evaluate experiments autonomously.",

    "How do I choose the number of initial experiments (n_init) for Bayesian Optimization?":
        "A common rule is to set `n_init = 2 to 3 × number of variables`. This helps the surrogate model (e.g., Gaussian Process) build a good initial understanding of the system.",

    "When should I stop a Bayesian Optimization campaign if I’m not using a fixed number of iterations?":
        "You can stop when recent experiments no longer significantly improve the result (convergence), or when the acquisition function suggests very low improvement potential.",

    "What do the acquisition functions (EI, PI, LCB) mean, and how do I pick one?":
        "EI (Expected Improvement) balances exploration and exploitation. PI (Probability of Improvement) is more conservative. LCB (Lower Confidence Bound) is more exploratory. EI is usually a safe default.",

    "Can I run VOL if I don’t have automated hardware or inline analytics?":
        "Yes! You can use the manual mode to suggest experiments and input results yourself. VOL still guides the optimization intelligently.",

    "What does the 'Yield' graph show, and why is it useful during optimization?":
        "The yield graph shows the best result found over time. It helps you track progress, detect plateaus, and know when the optimization might be complete.",

    "How is the 'next suggestion' generated, and what data is used to make it?":
        "VOL uses the surrogate model (built from your past data) and the acquisition function to choose the next experimental condition with the highest potential.",

    "How do I export my optimization results, and what formats are supported?":
        "You can export results as a CSV file after the campaign ends. Support for Excel and database export is being developed.",

    "Can I pause and resume an optimization campaign?":
        "Yes, VOL stores your session state and campaign data so you can resume where you left off, especially in automated mode.",

    "Is it possible to optimize more than one objective (e.g., Yield and Productivity)?":
        "Yes! VOL supports multi-objective optimization. You can select more than one target and explore trade-offs using Pareto front analysis."
}

for question, answer in faqs.items():
    with st.expander(question):
        st.markdown(answer)