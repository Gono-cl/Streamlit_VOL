"""Shared measurement-source controls for VOL execution pages."""
from __future__ import annotations

import math

import streamlit as st

from core.hardware.uvvis_measurement import UVVIS_DEFAULTS, UVVisMeasurement, measurement_settings


def render_measurement_settings():
    settings = measurement_settings(st.session_state)
    st.session_state.setdefault("measurement_backend", settings["measurement_backend"])
    st.sidebar.markdown("Measurement Source")
    backend = st.sidebar.selectbox(
        "Measurement method",
        ["opc", "uvvis"],
        format_func=lambda value: {"opc": "OPC signal", "uvvis": "UV/VIS (Spectragryph CSV)"}[value],
        key="measurement_backend",
    )
    if backend == "opc":
        st.session_state.setdefault("measurement_source_prefix", "OpusOPCSvr.HP-CZC3484P17->")
        st.session_state.setdefault("measurement_source_signal", "PDA - mM")
        prefix = st.sidebar.text_input("Measurement OPC Prefix", key="measurement_source_prefix")
        signal = st.sidebar.text_input("Measurement Signal", key="measurement_source_signal")
        st.sidebar.caption(f"Current measurement tag: `{prefix}{signal}`")
        return

    config = settings["uvvis_config"]
    for name, value in config.items():
        st.session_state.setdefault(f"uvvis_{name}", value)
    st.sidebar.text_input("Spectragryph export folder", key="uvvis_spectra_folder")
    st.sidebar.text_input("Saved calibration JSON", key="uvvis_calibration_file")
    st.sidebar.text_input(
        "Concentration unit",
        key="uvvis_concentration_unit",
        help="Use the calibration unit, for example mg/L for MB. Match inlet concentration units when calculating yield or degradation. This label does not convert coefficients.",
    )
    with st.sidebar.expander("UV/VIS acquisition settings"):
        st.text_input("Spectrum filename pattern", key="uvvis_file_pattern")
        st.number_input("Replicate spectra", min_value=2, step=1, key="uvvis_n_replicates")
        st.number_input("Replicate interval (s)", min_value=0.0, step=0.5, key="uvvis_replicate_interval")
        mode = st.selectbox(
            "Stability criterion", ["cv", "sd", "auto"], key="uvvis_stability_mode",
            format_func=lambda value: {"cv": "CV (%)", "sd": "Standard deviation (AU)",
                                       "auto": "Automatic CV / SD"}[value])
        st.number_input("Maximum absorbance CV (%)", min_value=0.0, step=0.5, key="uvvis_cv_threshold")
        sd_limit = st.number_input(
            "Maximum absorbance SD (AU)", min_value=0.0, value=None, step=0.0001,
            format="%.6f", key="uvvis_sd_threshold", placeholder="Enter a validated SD limit",
            help="Required for SD and automatic mode. Choose from blank and low-standard repeatability measurements.")
        if mode != "cv" and (sd_limit is None or sd_limit <= 0):
            st.warning("Enter a positive SD limit before running SD or automatic measurements.")
        if mode == "auto":
            st.caption("Accept when SD is within the larger of the absolute SD limit and the CV-based allowance. "
                       "The absolute limit governs low signals; the CV limit governs higher signals.")
        trend_enabled = st.checkbox("Check absorbance drift", key="uvvis_trend_enabled")
        drift_limit = st.number_input(
            "Maximum fitted drift across window (AU)", min_value=0.0, value=None,
            step=0.001, format="%.6f", key="uvvis_trend_threshold",
            help="Maximum absolute change of a line fitted against spectrum export timestamps. "
                 "The initial 0.010 AU limit is a starting setting; validate it with a stable solution.")
        if trend_enabled and (drift_limit is None or drift_limit <= 0):
            st.warning("Enter a positive drift limit before running the trend check.")
        st.caption("When enabled, drift and the selected SD/CV rule must both pass. "
                   "The latest consecutive spectra are used; increasing and decreasing drift are checked.")
        st.number_input("Maximum extra spectra", min_value=0, step=1, key="uvvis_max_extra_measurements")
        st.number_input("Timeout per new spectrum (s)", min_value=0.1, step=1.0, key="uvvis_spectrum_timeout")
    config = {name: st.session_state[f"uvvis_{name}"] for name in UVVIS_DEFAULTS}
    st.session_state.uvvis_config = config
    st.sidebar.caption("Spectragryph must continuously export CSV files with incrementing filenames. Each measurement waits for new spectra.")

    if st.sidebar.button("Test UV/VIS measurement", key="uvvis_test_measurement"):
        st.session_state.uvvis_last_test = None
        try:
            with st.spinner("Collecting fresh UV/VIS spectra..."):
                provider = UVVisMeasurement(config)
                result = provider.measure()
            st.session_state.uvvis_last_test = {"config": dict(config), "result": result}
        except Exception as exc:
            st.sidebar.error(str(exc))

    saved = st.session_state.get("uvvis_last_test")
    if saved and saved["config"] == config:
        result = saved["result"]
        st.sidebar.metric("Measured concentration", f"{result['concentration']:.5g} {result['concentration_unit']}")
        cv = result["absorbance_cv"]
        cv_text = f"{cv:.2f}%" if math.isfinite(cv) else "undefined"
        st.sidebar.caption(f"Absorbance: {result['absorbance']:.5g} AU · "
                           f"SD: {result['absorbance_sd']:.5g} AU · CV: {cv_text} · {result['wavelength']:.3f} nm")
        if "stability_criterion" in result:
            st.sidebar.caption(f"Decision: {result['stability_criterion'].upper()} ≤ "
                               f"{result['stability_threshold']:g} {result['stability_units']}")
        if result.get("trend_enabled"):
            if result.get("trend_error"):
                st.sidebar.caption(f"Trend check failed: {result['trend_error']}")
            else:
                st.sidebar.caption(
                    f"Fitted drift: {result['trend_fitted_change_au']:+.5g} AU over "
                    f"{result['trend_window_duration_s']:.2f} s; "
                    f"slope: {result['trend_slope_au_per_min']:+.5g} AU/min; "
                    f"limit: {result['trend_threshold_au']:g} AU; "
                    f"{'passed' if result['trend_accepted'] else 'failed'}")
        if result["accepted"] and result["status"] == "valid":
            st.sidebar.success("Validated measurement: stable and within calibration range.")
        else:
            st.sidebar.error(f"Measurement rejected: stability accepted={result['accepted']}; range={result['status']}.")
        with st.sidebar.expander("UV/VIS measurement details"):
            st.json({key: value for key, value in result.items() if key not in ("acquisitions", "absorbance_cv")})

    if st.session_state.get("simulation_mode", "off") != "off":
        st.sidebar.caption("Optimization uses simulated measurements in this mode. The test button reads actual exported spectra without controlling hardware.")
