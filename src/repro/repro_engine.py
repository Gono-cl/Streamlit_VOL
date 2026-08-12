"""Reproducibility engine for autonomous flow electrochemistry workflows."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import pandas as pd

from .policies import compute_drift_slope, compute_rsd, decide, merged_thresholds


class ReplicatePattern(str, Enum):
    """Supported replicate patterns."""

    IMMEDIATE = "immediate"
    BRACKETED = "bracketed"
    CYCLIC = "cyclic"
    SENTINEL = "sentinel"


class DecisionLabel(str, Enum):
    """Reproducibility decision labels."""

    PASS = "PASS"
    CONDITIONAL = "CONDITIONAL"
    FAIL = "FAIL"


@dataclass(frozen=True)
class Point:
    """A design-space point."""

    id: str
    values: dict[str, float]
    tags: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunContext:
    """Execution metadata attached to a single run."""

    run_index: int
    timestamp_utc: str
    pattern: ReplicatePattern
    group_id: str
    replicate_role: str
    is_sentinel: bool = False
    cleaning_level: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunResult:
    """Normalized run output."""

    objective: float
    output: dict[str, Any] = field(default_factory=dict)
    success: bool = True
    error: str | None = None


@dataclass(frozen=True)
class RunRecord:
    """One executed run record (point + context + result)."""

    run_id: str
    point: Point
    context: RunContext
    result: RunResult


class RunnerAdapter:
    """
    Adapter for existing runner signatures.

    Wraps callables shaped like:
      run_experiment(point: dict, metadata: dict) -> dict
    """

    def __init__(
        self,
        run_experiment: Callable[[dict[str, float], dict[str, Any]], Mapping[str, Any]],
        objective_key: str = "objective",
    ) -> None:
        self._run_experiment = run_experiment
        self._objective_key = objective_key

    def __call__(self, point: Point, context: RunContext) -> RunResult:
        payload = dict(point.values)
        metadata = {
            "run_index": context.run_index,
            "timestamp_utc": context.timestamp_utc,
            "pattern": context.pattern.value,
            "group_id": context.group_id,
            "replicate_role": context.replicate_role,
            "is_sentinel": context.is_sentinel,
            "cleaning_level": context.cleaning_level,
            "point_id": point.id,
            "point_tags": dict(point.tags),
        }
        metadata.update(dict(context.metadata))

        try:
            raw = self._run_experiment(payload, metadata)
            output = dict(raw) if isinstance(raw, Mapping) else {self._objective_key: raw}
            objective = self._extract_objective(output)
            return RunResult(objective=objective, output=output, success=True, error=None)
        except Exception as exc:  # pragma: no cover - exercised via failure handling
            return RunResult(objective=float("nan"), output={}, success=False, error=str(exc))

    def _extract_objective(self, output: Mapping[str, Any]) -> float:
        if self._objective_key in output:
            return float(output[self._objective_key])
        for value in output.values():
            try:
                return float(value)
            except Exception:
                continue
        raise KeyError(f"Runner output does not contain objective key '{self._objective_key}'.")


class ReproducibilityEngine:
    """
    Orchestrates reproducibility patterns, logging, analysis, and cleaning escalation.

    This module is hardware-agnostic by delegating all execution to a runner callable.
    """

    def __init__(
        self,
        runner: RunnerAdapter | Callable[[dict[str, float], dict[str, Any]], Mapping[str, Any]],
        objective_key: str = "objective",
        thresholds: Mapping[str, float] | None = None,
    ) -> None:
        self.runner = runner if isinstance(runner, RunnerAdapter) else RunnerAdapter(runner, objective_key=objective_key)
        self.thresholds = merged_thresholds(thresholds)
        self.records: list[RunRecord] = []
        self.adjudication_events: list[dict[str, Any]] = []
        self.cleaning_level = 0
        self._next_run_index = 0
        self._downgrade_pass_to_conditional_on_adjudication = True

    def schedule_with_reproducibility(
        self,
        test_points: Sequence[Point | Mapping[str, float]],
        patterns: Sequence[ReplicatePattern | str] | None = None,
        cyclic_repeats: int = 3,
        sentinel_point: Point | Mapping[str, float] | None = None,
        sentinel_every_n_runs: int | None = None,
        base_metadata: Mapping[str, Any] | None = None,
        phase: str = "baseline",
    ) -> list[RunRecord]:
        """
        Execute reproducibility schedule with immediate, bracketed, cyclic, and optional sentinel runs.
        """
        points = self._coerce_points(test_points, prefix="test")
        norm_patterns = self._normalize_patterns(patterns)
        sentinel = self._coerce_point(sentinel_point, point_id="sentinel") if sentinel_point is not None else None
        cyclic_repeats_count = max(1, int(cyclic_repeats))
        interval = int(sentinel_every_n_runs) if sentinel_every_n_runs else None
        if interval is not None and interval <= 0:
            raise ValueError("sentinel_every_n_runs must be > 0 when provided.")

        base_specs = self._build_base_specs(points, norm_patterns, phase=phase, cyclic_repeats=cyclic_repeats_count)
        metadata = dict(base_metadata or {})
        metadata.setdefault("phase", phase)
        metadata.setdefault("cyclic_repeats", cyclic_repeats_count)

        new_records: list[RunRecord] = []
        for point, pattern, group_id, role in base_specs:
            if sentinel is not None and interval is not None and self._next_run_index > 0 and self._next_run_index % interval == 0:
                new_records.append(
                    self._execute(
                        point=sentinel,
                        pattern=ReplicatePattern.SENTINEL,
                        group_id=f"{phase}_sentinel_{self._next_run_index}",
                        role="S",
                        is_sentinel=True,
                        metadata=metadata,
                    )
                )

            new_records.append(
                self._execute(
                    point=point,
                    pattern=pattern,
                    group_id=group_id,
                    role=role,
                    is_sentinel=False,
                    metadata=metadata,
                )
            )

        self.records.extend(new_records)
        return new_records

    def analyze(
        self,
        records: Sequence[RunRecord] | None = None,
        thresholds: Mapping[str, float] | None = None,
    ) -> dict[str, Any]:
        """Compute reproducibility metrics and decision label."""
        raw_records = list(records) if records is not None else list(self.records)
        run_records = self.effective_records(records=raw_records)
        th = dict(self.thresholds)
        if thresholds:
            th.update({str(k): float(v) for k, v in thresholds.items()})
        selected_events = self._select_adjudication_events(raw_records)
        confirmed_events = [event for event in selected_events if str(event.get("status")) == "confirmed_outlier"]
        unresolved_events = [event for event in selected_events if str(event.get("status")) == "unresolved"]

        immediate_pairs = self._extract_immediate_pairs(run_records)
        bracketed_groups = self._extract_bracketed_groups(run_records)
        cyclic_groups = self._extract_cyclic_groups(run_records)
        sentinel_df = self._extract_sentinel(run_records)

        immediate_abs_devs = immediate_pairs["abs_deviation"].to_numpy(dtype=float) if not immediate_pairs.empty else np.array([])
        immediate_rsds = immediate_pairs["pair_rsd_pct"].to_numpy(dtype=float) if not immediate_pairs.empty else np.array([])
        immediate_diffs = immediate_pairs["diff"].to_numpy(dtype=float) if not immediate_pairs.empty else np.array([])
        cyclic_abs_devs = cyclic_groups["abs_deviation_median"].to_numpy(dtype=float) if not cyclic_groups.empty else np.array([])
        cyclic_rsds = cyclic_groups["group_rsd_pct"].to_numpy(dtype=float) if not cyclic_groups.empty else np.array([])
        combined_abs_devs = (
            np.concatenate([immediate_abs_devs, cyclic_abs_devs])
            if immediate_abs_devs.size and cyclic_abs_devs.size
            else (immediate_abs_devs if immediate_abs_devs.size else cyclic_abs_devs)
        )
        combined_rsds = (
            np.concatenate([immediate_rsds, cyclic_rsds])
            if immediate_rsds.size and cyclic_rsds.size
            else (immediate_rsds if immediate_rsds.size else cyclic_rsds)
        )

        if immediate_diffs.size >= 2:
            noise_sigma = float(np.std(immediate_diffs, ddof=1) / np.sqrt(2.0))
        elif immediate_diffs.size == 1:
            noise_sigma = float(abs(immediate_diffs[0]) / np.sqrt(2.0))
        else:
            noise_sigma = float("nan")
        averaged_sigma = float(noise_sigma / np.sqrt(2.0)) if np.isfinite(noise_sigma) else float("nan")

        memory_abs = bracketed_groups["abs_shift"].to_numpy(dtype=float) if not bracketed_groups.empty else np.array([])

        sentinel_values = sentinel_df["objective"].tolist() if not sentinel_df.empty else []
        sentinel_idx = sentinel_df["run_index"].tolist() if not sentinel_df.empty else []
        sentinel_slope = compute_drift_slope(sentinel_values, sentinel_idx) if len(sentinel_values) >= 2 else float("nan")
        sentinel_slope_abs = abs(sentinel_slope) if np.isfinite(sentinel_slope) else float("nan")

        metrics = {
            "immediate_rsd_pct": _median_or_nan(combined_rsds),
            "immediate_abs_deviation_median": _median_or_nan(combined_abs_devs),
            "immediate_noise_sigma": noise_sigma,
            "immediate_averaged_sigma": averaged_sigma,
            "memory_abs_shift_median": _median_or_nan(memory_abs),
            "sentinel_drift_slope": sentinel_slope,
            "sentinel_drift_slope_abs": sentinel_slope_abs,
        }

        decision_raw, reasons = decide(metrics, thresholds=th)
        decision = DecisionLabel(decision_raw)
        pass_downgraded_to_conditional = False
        if confirmed_events:
            adjudication_note = (
                f"Cyclic outlier adjudication resolved {len(confirmed_events)} point(s)."
            )
            reasons = list(reasons)
            if decision == DecisionLabel.PASS and self._downgrade_pass_to_conditional_on_adjudication:
                decision = DecisionLabel.CONDITIONAL
                pass_downgraded_to_conditional = True
                reasons.append(f"{adjudication_note} PASS downgraded to CONDITIONAL.")
            else:
                reasons.append(adjudication_note)
        drift_detected = bool(np.isfinite(sentinel_slope_abs) and sentinel_slope_abs > th["drift_pass_slope"])

        total = len(raw_records)
        successful = sum(1 for r in raw_records if r.result.success and np.isfinite(r.result.objective))
        effective_total = len(run_records)
        effective_successful = sum(1 for r in run_records if r.result.success and np.isfinite(r.result.objective))

        return {
            "decision": decision,
            "reasons": reasons,
            "metrics": metrics,
            "thresholds": th,
            "drift_detected": drift_detected,
            "counts": {
                "total_runs": total,
                "successful_runs": successful,
                "failed_runs": total - successful,
                "effective_runs": effective_total,
                "effective_successful_runs": effective_successful,
                "excluded_outlier_runs": int(len(self._confirmed_outlier_run_ids(raw_records))),
                "immediate_pairs": int(len(immediate_pairs)),
                "bracketed_groups": int(len(bracketed_groups)),
                "cyclic_groups": int(len(cyclic_groups)),
                "sentinel_runs": int(len(sentinel_df)),
                "adjudication_confirmed_outliers": int(len(confirmed_events)),
                "adjudication_unresolved": int(len(unresolved_events)),
                "adjudication_extra_runs": int(len(selected_events)),
            },
            "adjudication": {
                "downgrade_pass_to_conditional": bool(self._downgrade_pass_to_conditional_on_adjudication),
                "pass_downgraded_to_conditional": bool(pass_downgraded_to_conditional),
                "confirmed_outliers": int(len(confirmed_events)),
                "unresolved_events": int(len(unresolved_events)),
                "events": selected_events,
            },
        }

    def effective_records(self, records: Sequence[RunRecord] | None = None) -> list[RunRecord]:
        """Return records after excluding confirmed cyclic outlier runs."""
        raw_records = list(records) if records is not None else list(self.records)
        excluded_run_ids = self._confirmed_outlier_run_ids(raw_records)
        if not excluded_run_ids:
            return raw_records
        return [record for record in raw_records if record.run_id not in excluded_run_ids]

    def adjudicate_cyclic_outliers(
        self,
        *,
        thresholds: Mapping[str, float] | None = None,
        pair_rsd_threshold_pct: float | None = None,
        outlier_gap_threshold_pct: float | None = None,
        max_extra_replicates_per_point: int = 1,
        downgrade_pass_to_conditional: bool = True,
        base_metadata: Mapping[str, Any] | None = None,
        repeat_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """
        Detect suspicious cyclic replicate groups and run one extra replicate for adjudication.

        A point is considered suspicious when removing one cyclic replicate leaves a tight cluster
        and that removed replicate sits far from the remaining cluster. The extra run is used to
        confirm or reject the outlier.
        """
        th = dict(self.thresholds)
        if thresholds:
            th.update({str(k): float(v) for k, v in thresholds.items()})

        pair_limit = float(
            pair_rsd_threshold_pct
            if pair_rsd_threshold_pct is not None
            else th.get("cyclic_outlier_pair_rsd_pct", 5.0)
        )
        gap_limit = float(
            outlier_gap_threshold_pct
            if outlier_gap_threshold_pct is not None
            else th.get("cyclic_outlier_gap_pct", 10.0)
        )
        max_extra = max(0, int(max_extra_replicates_per_point))
        self._downgrade_pass_to_conditional_on_adjudication = bool(downgrade_pass_to_conditional)

        if max_extra <= 0:
            return {
                "pair_rsd_threshold_pct": pair_limit,
                "outlier_gap_threshold_pct": gap_limit,
                "events": [],
                "extra_runs": [],
            }

        events: list[dict[str, Any]] = []
        extra_runs: list[RunRecord] = []
        for candidate in self._find_suspicious_cyclic_groups(
            pair_rsd_threshold_pct=pair_limit,
            outlier_gap_threshold_pct=gap_limit,
            max_extra_replicates_per_point=max_extra,
        ):
            preview = {
                "group_id": candidate["group_id"],
                "point_id": candidate["point"].id,
                "phase": candidate["phase"],
                "repeat_role": f"C{len(candidate['primary_group']) + 1}_ADJ",
            }
            if repeat_callback is not None:
                try:
                    repeat_callback(preview)
                except Exception:
                    pass

            repeat_metadata = dict(base_metadata or {})
            repeat_metadata.update(
                {
                    "phase": candidate["phase"],
                    "adjudication": "cyclic_outlier_repeat",
                    "adjudication_group_id": candidate["group_id"],
                    "adjudication_candidate_run_id": candidate["candidate_record"].run_id,
                    "adjudication_pair_run_ids": [rec.run_id for rec in candidate["pair_records"]],
                }
            )
            repeat_record = self._execute(
                point=candidate["point"],
                pattern=ReplicatePattern.CYCLIC,
                group_id=str(candidate["group_id"]),
                role=preview["repeat_role"],
                is_sentinel=False,
                metadata=repeat_metadata,
            )
            self.records.append(repeat_record)
            extra_runs.append(repeat_record)

            event = self._build_cyclic_adjudication_event(
                candidate=candidate,
                repeat_record=repeat_record,
                pair_rsd_threshold_pct=pair_limit,
                outlier_gap_threshold_pct=gap_limit,
            )
            self.adjudication_events.append(event)
            events.append(event)

        return {
            "pair_rsd_threshold_pct": pair_limit,
            "outlier_gap_threshold_pct": gap_limit,
            "events": events,
            "extra_runs": extra_runs,
        }

    def escalate_cleaning_and_retest(
        self,
        selected_points: Sequence[Point | Mapping[str, float]] | None = None,
        sentinel_point: Point | Mapping[str, float] | None = None,
        sentinel_every_n_runs: int | None = None,
        patterns: Sequence[ReplicatePattern | str] | None = None,
        cyclic_repeats: int = 3,
        thresholds: Mapping[str, float] | None = None,
        max_cleaning_level: int = 3,
        base_metadata: Mapping[str, Any] | None = None,
        cleaning_callback: Callable[[int], None] | None = None,
        adjudication_config: Mapping[str, Any] | None = None,
        adjudication_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """
        Escalate cleaning level and retest points if FAIL or drift is detected.
        """
        history: list[dict[str, Any]] = []
        analysis = self.analyze(thresholds=thresholds)
        history.append({"cleaning_level": self.cleaning_level, "analysis": analysis})

        while (
            analysis["decision"] == DecisionLabel.FAIL or analysis["drift_detected"]
        ) and self.cleaning_level < int(max_cleaning_level):
            self.cleaning_level += 1
            if cleaning_callback is not None:
                try:
                    cleaning_callback(self.cleaning_level)
                except Exception:
                    pass
            retest_points = (
                self._coerce_points(selected_points, prefix=f"retest_c{self.cleaning_level}")
                if selected_points
                else self._default_retest_points()
            )
            if not retest_points and sentinel_point is not None:
                retest_points = [self._coerce_point(sentinel_point, point_id=f"sentinel_retest_c{self.cleaning_level}")]
            if not retest_points:
                break

            run_meta = dict(base_metadata or {})
            run_meta.update(
                {
                    "phase": "cleaning_retest",
                    "cleaning_level": self.cleaning_level,
                }
            )
            self.schedule_with_reproducibility(
                test_points=retest_points,
                patterns=patterns or [ReplicatePattern.IMMEDIATE],
                cyclic_repeats=cyclic_repeats,
                sentinel_point=sentinel_point,
                sentinel_every_n_runs=sentinel_every_n_runs,
                base_metadata=run_meta,
                phase=f"cleaning_{self.cleaning_level}",
            )
            if adjudication_config:
                self.adjudicate_cyclic_outliers(
                    base_metadata=run_meta,
                    repeat_callback=adjudication_callback,
                    **dict(adjudication_config),
                )
            analysis = self.analyze(thresholds=thresholds)
            history.append({"cleaning_level": self.cleaning_level, "analysis": analysis})

        return {
            "cleaning_level": self.cleaning_level,
            "history": history,
            "final_analysis": analysis,
        }

    def to_dataframe(self, records: Sequence[RunRecord] | None = None) -> pd.DataFrame:
        """Convert run records to a flat DataFrame with rich metadata fields."""
        run_records = list(records) if records is not None else list(self.records)
        rows: list[dict[str, Any]] = []
        for record in run_records:
            row: dict[str, Any] = {
                "run_id": record.run_id,
                "run_index": record.context.run_index,
                "timestamp_utc": record.context.timestamp_utc,
                "pattern": record.context.pattern.value,
                "group_id": record.context.group_id,
                "replicate_role": record.context.replicate_role,
                "is_sentinel": bool(record.context.is_sentinel),
                "cleaning_level": int(record.context.cleaning_level),
                "point_id": record.point.id,
                "success": bool(record.result.success),
                "objective": float(record.result.objective),
                "error": record.result.error or "",
                "point_json": json.dumps(record.point.values, sort_keys=True),
                "point_tags_json": json.dumps(record.point.tags, sort_keys=True),
                "run_metadata_json": json.dumps(record.context.metadata, sort_keys=True),
                "output_json": json.dumps(record.result.output, sort_keys=True),
            }
            for key, value in sorted(record.point.values.items()):
                row[f"x_{key}"] = float(value)
            rows.append(row)
        return pd.DataFrame(rows)

    def save_csv_logs(self, path: str | Path, records: Sequence[RunRecord] | None = None) -> pd.DataFrame:
        """Save run records to CSV."""
        df = self.to_dataframe(records=records)
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(target, index=False)
        return df

    def load_csv_logs(self, path: str | Path, append: bool = False) -> list[RunRecord]:
        """Load records from CSV log format created by this engine."""
        df = pd.read_csv(path)
        loaded: list[RunRecord] = []

        for i, row in df.iterrows():
            row_dict = row.to_dict()
            point_values = _safe_json_loads(row_dict.get("point_json"), default={})
            if not point_values:
                point_values = {
                    str(k)[2:]: float(v)
                    for k, v in row_dict.items()
                    if str(k).startswith("x_") and pd.notna(v)
                }
            point = Point(
                id=str(row_dict.get("point_id", f"loaded_point_{i:04d}")),
                values={str(k): float(v) for k, v in dict(point_values).items()},
                tags=_safe_json_loads(row_dict.get("point_tags_json"), default={}),
            )

            pattern = _parse_pattern(row_dict.get("pattern"))
            run_index = int(row_dict.get("run_index", i))
            context = RunContext(
                run_index=run_index,
                timestamp_utc=str(row_dict.get("timestamp_utc", "")),
                pattern=pattern,
                group_id=str(row_dict.get("group_id", f"loaded_group_{i:04d}")),
                replicate_role=str(row_dict.get("replicate_role", "")),
                is_sentinel=_parse_bool(row_dict.get("is_sentinel", False)),
                cleaning_level=int(row_dict.get("cleaning_level", 0)),
                metadata=_safe_json_loads(row_dict.get("run_metadata_json"), default={}),
            )
            result = RunResult(
                objective=float(row_dict.get("objective", float("nan"))),
                output=_safe_json_loads(row_dict.get("output_json"), default={}),
                success=_parse_bool(row_dict.get("success", True)),
                error=str(row_dict.get("error", "")) or None,
            )
            loaded.append(
                RunRecord(
                    run_id=str(row_dict.get("run_id", f"loaded_run_{i:06d}")),
                    point=point,
                    context=context,
                    result=result,
                )
            )

        if append:
            self.records.extend(loaded)
        else:
            self.records = loaded
        if self.records:
            self._next_run_index = max(r.context.run_index for r in self.records) + 1
            self.cleaning_level = max(int(r.context.cleaning_level) for r in self.records)
        return loaded

    def _build_base_specs(
        self,
        points: Sequence[Point],
        patterns: Sequence[ReplicatePattern],
        phase: str,
        cyclic_repeats: int,
    ) -> list[tuple[Point, ReplicatePattern, str, str]]:
        specs: list[tuple[Point, ReplicatePattern, str, str]] = []
        for pattern in patterns:
            if pattern == ReplicatePattern.IMMEDIATE:
                for i, a in enumerate(points):
                    group_id = f"{phase}_immediate_{i:03d}_c{self.cleaning_level}"
                    specs.append((a, ReplicatePattern.IMMEDIATE, group_id, "A1"))
                    specs.append((a, ReplicatePattern.IMMEDIATE, group_id, "A2"))
            elif pattern == ReplicatePattern.BRACKETED:
                for i, a in enumerate(points):
                    b = points[(i + 1) % len(points)] if len(points) > 1 else a
                    group_id = f"{phase}_bracketed_{i:03d}_c{self.cleaning_level}"
                    specs.append((a, ReplicatePattern.BRACKETED, group_id, "A_PRE"))
                    specs.append((b, ReplicatePattern.BRACKETED, group_id, "B"))
                    specs.append((a, ReplicatePattern.BRACKETED, group_id, "A_POST"))
            elif pattern == ReplicatePattern.CYCLIC:
                for cycle_idx in range(max(1, int(cyclic_repeats))):
                    role = f"C{cycle_idx + 1}"
                    for i, a in enumerate(points):
                        group_id = f"{phase}_cyclic_{i:03d}_c{self.cleaning_level}"
                        specs.append((a, ReplicatePattern.CYCLIC, group_id, role))
            elif pattern == ReplicatePattern.SENTINEL:
                continue
            else:  # pragma: no cover - safety branch
                raise ValueError(f"Unsupported pattern '{pattern}'.")
        return specs

    def _execute(
        self,
        point: Point,
        pattern: ReplicatePattern,
        group_id: str,
        role: str,
        is_sentinel: bool,
        metadata: Mapping[str, Any] | None,
    ) -> RunRecord:
        context = RunContext(
            run_index=self._next_run_index,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            pattern=pattern,
            group_id=group_id,
            replicate_role=role,
            is_sentinel=is_sentinel,
            cleaning_level=self.cleaning_level,
            metadata=dict(metadata or {}),
        )
        result = self.runner(point, context)
        record = RunRecord(
            run_id=f"run_{self._next_run_index:06d}",
            point=point,
            context=context,
            result=result,
        )
        self._next_run_index += 1
        return record

    def _coerce_points(
        self,
        points: Sequence[Point | Mapping[str, float]] | None,
        prefix: str,
    ) -> list[Point]:
        out: list[Point] = []
        for idx, point in enumerate(points or []):
            out.append(self._coerce_point(point, point_id=f"{prefix}_{idx:03d}"))
        return out

    def _coerce_point(
        self,
        point: Point | Mapping[str, float] | None,
        point_id: str,
    ) -> Point:
        if point is None:
            raise ValueError("Point cannot be None.")
        if isinstance(point, Point):
            return point
        values = {str(k): float(v) for k, v in point.items()}
        return Point(id=point_id, values=values)

    def _normalize_patterns(self, patterns: Sequence[ReplicatePattern | str] | None) -> list[ReplicatePattern]:
        raw = patterns or [ReplicatePattern.IMMEDIATE, ReplicatePattern.BRACKETED]
        out: list[ReplicatePattern] = []
        for pattern in raw:
            out.append(pattern if isinstance(pattern, ReplicatePattern) else ReplicatePattern(str(pattern)))
        return out

    def _default_retest_points(self) -> list[Point]:
        seen: set[str] = set()
        out: list[Point] = []
        for record in self.records:
            if record.context.is_sentinel:
                continue
            if record.point.id in seen:
                continue
            seen.add(record.point.id)
            out.append(record.point)
        return out[:3]

    def _extract_immediate_pairs(self, records: Sequence[RunRecord]) -> pd.DataFrame:
        grouped: dict[str, list[RunRecord]] = {}
        for rec in records:
            if rec.context.pattern != ReplicatePattern.IMMEDIATE or not rec.result.success:
                continue
            if not np.isfinite(rec.result.objective):
                continue
            grouped.setdefault(rec.context.group_id, []).append(rec)

        rows: list[dict[str, float]] = []
        for group_id, group in grouped.items():
            group_sorted = sorted(group, key=lambda r: r.context.run_index)
            if len(group_sorted) < 2:
                continue
            y1 = float(group_sorted[0].result.objective)
            y2 = float(group_sorted[1].result.objective)
            rows.append(
                {
                    "group_id": group_id,
                    "y1": y1,
                    "y2": y2,
                    "diff": y2 - y1,
                    "abs_deviation": abs(y2 - y1),
                    "pair_rsd_pct": compute_rsd([y1, y2]),
                }
            )
        return pd.DataFrame(rows)

    def _extract_bracketed_groups(self, records: Sequence[RunRecord]) -> pd.DataFrame:
        grouped: dict[str, list[RunRecord]] = {}
        for rec in records:
            if rec.context.pattern != ReplicatePattern.BRACKETED or not rec.result.success:
                continue
            if not np.isfinite(rec.result.objective):
                continue
            grouped.setdefault(rec.context.group_id, []).append(rec)

        rows: list[dict[str, float]] = []
        for group_id, group in grouped.items():
            group_sorted = sorted(group, key=lambda r: r.context.run_index)
            if len(group_sorted) < 3:
                continue
            a_pre = _first_objective(group_sorted, default_index=0, role="A_PRE")
            a_post = _first_objective(group_sorted, default_index=-1, role="A_POST")
            shift = a_post - a_pre
            rows.append(
                {
                    "group_id": group_id,
                    "a_pre": a_pre,
                    "a_post": a_post,
                    "shift": shift,
                    "abs_shift": abs(shift),
                }
            )
        return pd.DataFrame(rows)

    def _extract_cyclic_groups(self, records: Sequence[RunRecord]) -> pd.DataFrame:
        grouped: dict[str, list[RunRecord]] = {}
        for rec in records:
            if rec.context.pattern != ReplicatePattern.CYCLIC or not rec.result.success:
                continue
            if not np.isfinite(rec.result.objective):
                continue
            grouped.setdefault(rec.context.group_id, []).append(rec)

        rows: list[dict[str, float]] = []
        for group_id, group in grouped.items():
            group_sorted = sorted(group, key=lambda r: r.context.run_index)
            if len(group_sorted) < 2:
                continue
            vals = np.asarray([float(rec.result.objective) for rec in group_sorted], dtype=float)
            mean_val = float(np.mean(vals))
            rows.append(
                {
                    "group_id": group_id,
                    "n_reps": int(vals.size),
                    "group_rsd_pct": compute_rsd(vals.tolist()),
                    "abs_deviation_median": float(np.median(np.abs(vals - mean_val))),
                    "range_abs": float(np.max(vals) - np.min(vals)),
                }
            )
        return pd.DataFrame(rows)

    def _extract_sentinel(self, records: Sequence[RunRecord]) -> pd.DataFrame:
        rows: list[dict[str, float]] = []
        for rec in records:
            if rec.context.pattern != ReplicatePattern.SENTINEL and not rec.context.is_sentinel:
                continue
            if not rec.result.success or not np.isfinite(rec.result.objective):
                continue
            rows.append(
                {
                    "run_index": rec.context.run_index,
                    "objective": float(rec.result.objective),
                }
            )
        return pd.DataFrame(rows).sort_values("run_index") if rows else pd.DataFrame(columns=["run_index", "objective"])

    def _find_suspicious_cyclic_groups(
        self,
        *,
        pair_rsd_threshold_pct: float,
        outlier_gap_threshold_pct: float,
        max_extra_replicates_per_point: int,
    ) -> list[dict[str, Any]]:
        grouped: dict[str, list[RunRecord]] = {}
        for record in self.records:
            if record.context.pattern != ReplicatePattern.CYCLIC or record.context.is_sentinel:
                continue
            if not record.result.success or not np.isfinite(record.result.objective):
                continue
            grouped.setdefault(record.context.group_id, []).append(record)

        candidates: list[dict[str, Any]] = []
        for group_id, group in grouped.items():
            prior_events = sum(1 for event in self.adjudication_events if str(event.get("group_id")) == str(group_id))
            if prior_events >= max_extra_replicates_per_point:
                continue

            primary_group = [
                record
                for record in sorted(group, key=lambda rec: rec.context.run_index)
                if not str(record.context.replicate_role).upper().endswith("_ADJ")
            ]
            if len(primary_group) < 3:
                continue

            candidate = self._single_cyclic_outlier_candidate(
                primary_group=primary_group,
                pair_rsd_threshold_pct=pair_rsd_threshold_pct,
                outlier_gap_threshold_pct=outlier_gap_threshold_pct,
            )
            if candidate:
                candidate["group_id"] = group_id
                candidate["point"] = primary_group[0].point
                candidate["phase"] = str(primary_group[0].context.metadata.get("phase", "baseline"))
                candidate["primary_group"] = primary_group
                candidates.append(candidate)

        return sorted(
            candidates,
            key=lambda item: int(item["candidate_record"].context.run_index),
        )

    def _single_cyclic_outlier_candidate(
        self,
        *,
        primary_group: Sequence[RunRecord],
        pair_rsd_threshold_pct: float,
        outlier_gap_threshold_pct: float,
    ) -> dict[str, Any] | None:
        best: dict[str, Any] | None = None
        for idx, candidate_record in enumerate(primary_group):
            cluster_records = [record for pair_idx, record in enumerate(primary_group) if pair_idx != idx]
            if len(cluster_records) < 2:
                continue
            cluster_values = [float(record.result.objective) for record in cluster_records]
            cluster_rsd = compute_rsd(cluster_values)
            if not np.isfinite(cluster_rsd) or cluster_rsd > pair_rsd_threshold_pct:
                continue

            cluster_mean = float(np.mean(cluster_values))
            candidate_value = float(candidate_record.result.objective)
            gap_pct = _relative_gap_pct(candidate_value, cluster_mean)
            if not np.isfinite(gap_pct) or gap_pct < outlier_gap_threshold_pct:
                continue

            proposed = {
                "candidate_record": candidate_record,
                "candidate_value": candidate_value,
                "pair_records": cluster_records,
                "pair_values": cluster_values,
                "pair_mean": cluster_mean,
                "pair_rsd_pct": cluster_rsd,
                "outlier_gap_pct": gap_pct,
            }
            if best is None:
                best = proposed
                continue

            if float(proposed["pair_rsd_pct"]) < float(best["pair_rsd_pct"]):
                best = proposed
                continue
            if np.isclose(float(proposed["pair_rsd_pct"]), float(best["pair_rsd_pct"])) and float(
                proposed["outlier_gap_pct"]
            ) > float(best["outlier_gap_pct"]):
                best = proposed

        return best

    def _build_cyclic_adjudication_event(
        self,
        *,
        candidate: Mapping[str, Any],
        repeat_record: RunRecord,
        pair_rsd_threshold_pct: float,
        outlier_gap_threshold_pct: float,
    ) -> dict[str, Any]:
        pair_values = [float(value) for value in candidate["pair_values"]]
        repeat_value = float(repeat_record.result.objective)
        resolution_rsd = compute_rsd(pair_values + [repeat_value]) if repeat_record.result.success else float("nan")
        repeat_gap_pct = _relative_gap_pct(repeat_value, float(candidate["pair_mean"]))
        status = "unresolved"
        if not repeat_record.result.success or not np.isfinite(repeat_value):
            status = "repeat_failed"
        elif (
            np.isfinite(resolution_rsd)
            and resolution_rsd <= pair_rsd_threshold_pct
            and np.isfinite(repeat_gap_pct)
            and repeat_gap_pct <= outlier_gap_threshold_pct
        ):
            status = "confirmed_outlier"

        return {
            "event_id": f"adj_{len(self.adjudication_events):06d}",
            "pattern": ReplicatePattern.CYCLIC.value,
            "group_id": str(candidate["group_id"]),
            "phase": str(candidate["phase"]),
            "point_id": str(candidate["point"].id),
            "cleaning_level": int(repeat_record.context.cleaning_level),
            "status": status,
            "candidate_run_id": str(candidate["candidate_record"].run_id),
            "candidate_role": str(candidate["candidate_record"].context.replicate_role),
            "candidate_value": float(candidate["candidate_value"]),
            "cluster_run_ids": [str(record.run_id) for record in candidate["pair_records"]],
            "cluster_roles": [str(record.context.replicate_role) for record in candidate["pair_records"]],
            "cluster_values": pair_values,
            "cluster_pair_rsd_pct": float(candidate["pair_rsd_pct"]),
            "outlier_gap_pct": float(candidate["outlier_gap_pct"]),
            "repeat_run_id": str(repeat_record.run_id),
            "repeat_role": str(repeat_record.context.replicate_role),
            "repeat_value": repeat_value,
            "repeat_gap_to_cluster_pct": repeat_gap_pct,
            "resolution_rsd_pct": resolution_rsd,
            "pair_rsd_threshold_pct": float(pair_rsd_threshold_pct),
            "outlier_gap_threshold_pct": float(outlier_gap_threshold_pct),
        }

    def _select_adjudication_events(self, records: Sequence[RunRecord]) -> list[dict[str, Any]]:
        record_ids = {record.run_id for record in records}
        selected: list[dict[str, Any]] = []
        for event in self.adjudication_events:
            candidate_id = str(event.get("candidate_run_id", ""))
            repeat_id = str(event.get("repeat_run_id", ""))
            if candidate_id in record_ids or repeat_id in record_ids:
                selected.append(dict(event))
        return selected

    def _confirmed_outlier_run_ids(self, records: Sequence[RunRecord]) -> set[str]:
        excluded: set[str] = set()
        for event in self._select_adjudication_events(records):
            if str(event.get("status")) == "confirmed_outlier":
                excluded.add(str(event.get("candidate_run_id")))
        return excluded


def _safe_json_loads(raw: Any, default: Any) -> Any:
    if raw is None or (isinstance(raw, float) and np.isnan(raw)):
        return default
    try:
        return json.loads(str(raw))
    except Exception:
        return default


def _median_or_nan(values: np.ndarray) -> float:
    if values.size == 0:
        return float("nan")
    values = values[np.isfinite(values)]
    if values.size == 0:
        return float("nan")
    return float(np.median(values))


def _relative_gap_pct(value: float, reference: float) -> float:
    denom = max(abs(float(reference)), 1e-12)
    return abs(float(value) - float(reference)) / denom * 100.0


def _parse_pattern(raw: Any) -> ReplicatePattern:
    try:
        return ReplicatePattern(str(raw))
    except Exception:
        return ReplicatePattern.IMMEDIATE


def _parse_bool(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    return text in {"1", "true", "yes", "y", "t"}


def _first_objective(group_sorted: Sequence[RunRecord], default_index: int, role: str) -> float:
    for rec in group_sorted:
        if rec.context.replicate_role == role:
            return float(rec.result.objective)
    return float(group_sorted[default_index].result.objective)
