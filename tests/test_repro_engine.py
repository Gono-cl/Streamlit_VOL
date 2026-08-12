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


@dataclass
class SequencedCyclicRunner:
    responses: dict[tuple[str, str], float]

    def run_experiment(self, point: dict[str, float], metadata: dict[str, Any]) -> dict[str, float]:
        point_id = str(metadata.get("point_id", ""))
        role = str(metadata.get("replicate_role", ""))
        key = (point_id, role)
        if key not in self.responses:
            raise KeyError(f"Missing response for {key}.")
        return {"objective": float(self.responses[key])}


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
        "immediate_rsd_pct": 7.0,
        "immediate_abs_deviation_median": 0.02,
        "sentinel_drift_slope_abs": 0.001,
        "memory_abs_shift_median": 0.01,
    }
    fail_metrics = {
        "immediate_rsd_pct": 11.0,
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


def test_cyclic_outlier_repeat_confirms_transient_and_downgrades_to_conditional() -> None:
    runner = SequencedCyclicRunner(
        responses={
            ("test_000", "C1"): 1.00,
            ("test_000", "C2"): 1.02,
            ("test_000", "C3"): 0.75,
            ("test_000", "C4_ADJ"): 1.01,
        }
    )
    engine = ReproducibilityEngine(runner.run_experiment)

    engine.schedule_with_reproducibility(
        test_points=[{"x1": 0.4, "x2": 0.6}],
        patterns=[ReplicatePattern.CYCLIC],
        phase="cyclic_outlier_test",
    )
    adjudication = engine.adjudicate_cyclic_outliers()
    report = engine.analyze()

    assert len(adjudication["events"]) == 1
    assert adjudication["events"][0]["status"] == "confirmed_outlier"
    assert report["decision"] == DecisionLabel.CONDITIONAL
    assert report["counts"]["total_runs"] == 4
    assert report["counts"]["effective_runs"] == 3
    assert report["counts"]["excluded_outlier_runs"] == 1
    assert report["counts"]["adjudication_confirmed_outliers"] == 1
    assert report["adjudication"]["pass_downgraded_to_conditional"] is True
    assert "downgraded to CONDITIONAL" in " ".join(report["reasons"])


def test_cyclic_outlier_repeat_keeps_fail_when_extra_run_does_not_resolve_spread() -> None:
    runner = SequencedCyclicRunner(
        responses={
            ("test_000", "C1"): 1.00,
            ("test_000", "C2"): 1.02,
            ("test_000", "C3"): 0.75,
            ("test_000", "C4_ADJ"): 0.76,
        }
    )
    engine = ReproducibilityEngine(runner.run_experiment)

    engine.schedule_with_reproducibility(
        test_points=[{"x1": 0.4, "x2": 0.6}],
        patterns=[ReplicatePattern.CYCLIC],
        phase="cyclic_outlier_fail",
    )
    adjudication = engine.adjudicate_cyclic_outliers()
    report = engine.analyze()

    assert len(adjudication["events"]) == 1
    assert adjudication["events"][0]["status"] == "unresolved"
    assert report["decision"] == DecisionLabel.FAIL
    assert report["counts"]["excluded_outlier_runs"] == 0
    assert report["counts"]["adjudication_unresolved"] == 1


def test_cyclic_outlier_repeat_supports_five_replicates() -> None:
    runner = SequencedCyclicRunner(
        responses={
            ("test_000", "C1"): 60.0,
            ("test_000", "C2"): 60.5,
            ("test_000", "C3"): 59.8,
            ("test_000", "C4"): 60.2,
            ("test_000", "C5"): 44.0,
            ("test_000", "C6_ADJ"): 60.1,
        }
    )
    engine = ReproducibilityEngine(runner.run_experiment)

    scheduled = engine.schedule_with_reproducibility(
        test_points=[{"x1": 0.4, "x2": 0.6}],
        patterns=[ReplicatePattern.CYCLIC],
        cyclic_repeats=5,
        phase="cyclic_outlier_five",
    )
    adjudication = engine.adjudicate_cyclic_outliers()
    report = engine.analyze()

    assert len(scheduled) == 5
    assert len(adjudication["events"]) == 1
    assert adjudication["events"][0]["candidate_role"] == "C5"
    assert adjudication["events"][0]["repeat_role"] == "C6_ADJ"
    assert adjudication["events"][0]["status"] == "confirmed_outlier"
    assert report["decision"] == DecisionLabel.CONDITIONAL
    assert report["counts"]["total_runs"] == 6
    assert report["counts"]["effective_runs"] == 5
