"""Policy functions for reproducibility metrics and decisions."""

from __future__ import annotations

from typing import Mapping, Sequence

import numpy as np

DEFAULT_THRESHOLDS: dict[str, float] = {
    # Commissioning defaults (looser than strict mode for early real-lab deployment)
    "rsd_pass_pct": 5.0,
    "rsd_conditional_pct": 10.0,
    "abs_dev_pass": 0.08,
    "abs_dev_conditional": 2.0,
    "drift_pass_slope": 0.006,
    "drift_conditional_slope": 0.020,
    "memory_pass_abs_shift": 0.08,
    "memory_conditional_abs_shift": 0.20,
    "cyclic_outlier_pair_rsd_pct": 5.0,
    "cyclic_outlier_gap_pct": 10.0,
}


def compute_rsd(values: Sequence[float]) -> float:
    """Return relative standard deviation (RSD) in percent."""
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size < 2:
        return float("nan")
    mean = float(np.mean(arr))
    if np.isclose(mean, 0.0):
        return float("nan")
    std = float(np.std(arr, ddof=1))
    return abs(std / mean) * 100.0


def compute_drift_slope(sentinel_values: Sequence[float], sentinel_run_indices: Sequence[int]) -> float:
    """
    Compute linear drift slope from sentinel objective values over run index.

    Positive slope indicates objective drift upward with run order.
    """
    vals = np.asarray(sentinel_values, dtype=float)
    idx = np.asarray(sentinel_run_indices, dtype=float)
    mask = np.isfinite(vals) & np.isfinite(idx)
    vals = vals[mask]
    idx = idx[mask]
    if vals.size < 2:
        return float("nan")
    if vals.size != idx.size:
        raise ValueError("sentinel_values and sentinel_run_indices must have equal length.")
    if np.allclose(idx, idx[0]):
        return 0.0
    slope, _ = np.polyfit(idx, vals, 1)
    return float(slope)


def merged_thresholds(overrides: Mapping[str, float] | None = None) -> dict[str, float]:
    """Merge user threshold overrides with defaults."""
    out = dict(DEFAULT_THRESHOLDS)
    if overrides:
        for key, value in overrides.items():
            out[str(key)] = float(value)
    return out


def decide(metrics: Mapping[str, float], thresholds: Mapping[str, float] | None = None) -> tuple[str, list[str]]:
    """
    Classify reproducibility state as PASS / CONDITIONAL / FAIL.

    Metrics are treated as "lower is better". Missing/NaN metrics are skipped.
    """
    th = merged_thresholds(thresholds)
    rules = [
        ("immediate_rsd_pct", "Replicate RSD (%)", "rsd_pass_pct", "rsd_conditional_pct"),
        ("immediate_abs_deviation_median", "Replicate abs deviation", "abs_dev_pass", "abs_dev_conditional"),
        ("sentinel_drift_slope_abs", "Sentinel drift slope abs", "drift_pass_slope", "drift_conditional_slope"),
        ("memory_abs_shift_median", "Bracketed memory abs shift", "memory_pass_abs_shift", "memory_conditional_abs_shift"),
    ]

    fail_reasons: list[str] = []
    conditional_reasons: list[str] = []
    evaluated = 0

    for metric_key, metric_name, pass_key, cond_key in rules:
        raw_value = metrics.get(metric_key, float("nan"))
        value = float(raw_value) if raw_value is not None else float("nan")
        if not np.isfinite(value):
            continue
        evaluated += 1
        pass_thr = float(th[pass_key])
        cond_thr = float(th[cond_key])
        if value > cond_thr:
            fail_reasons.append(f"{metric_name}={value:.6g} > {cond_thr:.6g}")
        elif value > pass_thr:
            conditional_reasons.append(f"{metric_name}={value:.6g} > {pass_thr:.6g}")

    if fail_reasons:
        return "FAIL", fail_reasons
    if conditional_reasons:
        return "CONDITIONAL", conditional_reasons
    if evaluated == 0:
        return "CONDITIONAL", ["No valid reproducibility metrics were available."]
    return "PASS", []
