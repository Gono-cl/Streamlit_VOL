from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from src.repro.policies import decide
from src.repro.repro_engine import DecisionLabel, ReproducibilityEngine, ReplicatePattern


@dataclass
class DummyRunner:
    seed: int = 123
    noise_std: float = 0.0
    drift_per_run: float = 0.0
    memory_strength: float = 0.0

    def __post_init__(self) -> None:
        self.rng = np.random.default_rng(self.seed)
        self.prev_point: dict[str, float] | None = None

    def run_experiment(self, point: dict[str, float], metadata: dict[str, Any]) -> dict[str, float]:
        run_index = int(metadata.get("run_index", 0))
        base = 0.7 * float(point.get("x1", 0.0)) + 0.3 * float(point.get("x2", 0.0))
        drift = self.drift_per_run * run_index
        noise = float(self.rng.normal(0.0, self.noise_std))

        memory = 0.0
        if self.prev_point is not None and str(metadata.get("replicate_role")) == "A_POST":
            keys = set(self.prev_point.keys()).union(point.keys())
            distance = sum(abs(float(point.get(k, 0.0)) - float(self.prev_point.get(k, 0.0))) for k in keys)
            memory = self.memory_strength * distance

        self.prev_point = dict(point)
        return {"objective": base + drift + noise + memory}


def test_immediate_replicates_reduce_uncertainty_estimate() -> None:
    runner = DummyRunner(seed=7, noise_std=0.15, drift_per_run=0.0, memory_strength=0.0)
    engine = ReproducibilityEngine(runner.run_experiment)

    points = [{"x1": 0.4, "x2": 0.8} for _ in range(12)]
    engine.schedule_with_reproducibility(
        test_points=points,
        patterns=[ReplicatePattern.IMMEDIATE],
        phase="uncertainty_test",
    )
    report = engine.analyze()

    sigma_single = report["metrics"]["immediate_noise_sigma"]
    sigma_avg = report["metrics"]["immediate_averaged_sigma"]
    assert np.isfinite(sigma_single)
    assert np.isfinite(sigma_avg)
    assert sigma_avg < sigma_single


def test_bracketed_pattern_detects_memory() -> None:
    runner = DummyRunner(seed=11, noise_std=0.0, drift_per_run=0.0, memory_strength=0.3)
    engine = ReproducibilityEngine(runner.run_experiment)

    points = [
        {"x1": 0.0, "x2": 0.0},
        {"x1": 1.0, "x2": 1.0},
    ]
    engine.schedule_with_reproducibility(
        test_points=points,
        patterns=[ReplicatePattern.BRACKETED],
        phase="memory_test",
    )
    report = engine.analyze()

    assert report["metrics"]["memory_abs_shift_median"] > 0.1
    assert report["decision"] == DecisionLabel.FAIL


def test_sentinel_drift_is_detected() -> None:
    runner = DummyRunner(seed=19, noise_std=0.0, drift_per_run=0.04, memory_strength=0.0)
    engine = ReproducibilityEngine(runner.run_experiment)

    engine.schedule_with_reproducibility(
        test_points=[{"x1": 0.2, "x2": 0.1} for _ in range(10)],
        patterns=[ReplicatePattern.IMMEDIATE],
        sentinel_point={"x1": 0.5, "x2": 0.5},
        sentinel_every_n_runs=3,
        phase="drift_test",
    )
    report = engine.analyze()

    assert report["drift_detected"] is True
    assert report["metrics"]["sentinel_drift_slope_abs"] > report["thresholds"]["drift_pass_slope"]


def test_decision_labels_match_expected_outcomes() -> None:
    pass_metrics = {
        "immediate_rsd_pct": 1.0,
        "immediate_abs_deviation_median": 0.01,
        "sentinel_drift_slope_abs": 0.001,
        "memory_abs_shift_median": 0.01,
    }
    cond_metrics = {
        "immediate_rsd_pct": 3.0,
        "immediate_abs_deviation_median": 0.02,
        "sentinel_drift_slope_abs": 0.001,
        "memory_abs_shift_median": 0.01,
    }
    fail_metrics = {
        "immediate_rsd_pct": 6.0,
        "immediate_abs_deviation_median": 0.02,
        "sentinel_drift_slope_abs": 0.001,
        "memory_abs_shift_median": 0.01,
    }

    pass_label, _ = decide(pass_metrics)
    cond_label, _ = decide(cond_metrics)
    fail_label, _ = decide(fail_metrics)

    assert pass_label == "PASS"
    assert cond_label == "CONDITIONAL"
    assert fail_label == "FAIL"
