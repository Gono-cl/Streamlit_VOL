import numpy as np
import matplotlib.pyplot as plt
import streamlit as st
from skopt.acquisition import gaussian_ei


def plot_acquisition_1d(optimizer, resolution=200):
    skopt_opt = optimizer.skopt_optimizer
    if len(skopt_opt.space.dimensions) != 1:
        st.warning("Acquisition function visualization only supported for 1D at the moment.")
        return

    dim = skopt_opt.space.dimensions[0]
    x = np.linspace(dim.low, dim.high, resolution).reshape(-1, 1)
    acq = gaussian_ei(x, skopt_opt.models[-1], skopt_opt.space, skopt_opt.rng)

    fig, ax = plt.subplots()
    ax.plot(x, acq, label="Expected Improvement", color="tab:orange")
    ax.set_xlabel(dim.name or "x")
    ax.set_ylabel("Acquisition Value")
    ax.set_title("Acquisition Function (EI)")
    ax.legend()
    st.pyplot(fig)


