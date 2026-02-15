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
        self.cleaning_level = 0
        self._next_run_index = 0

    def schedule_with_reproducibility(
        self,
        test_points: Sequence[Point | Mapping[str, float]],
        patterns: Sequence[ReplicatePattern | str] | None = None,
        sentinel_point: Point | Mapping[str, float] | None = None,
        sentinel_every_n_runs: int | None = None,
        base_metadata: Mapping[str, Any] | None = None,
        phase: str = "baseline",
    ) -> list[RunRecord]:
        """
        Execute reproducibility schedule with immediate, bracketed, and optional sentinel runs.
        """
        points = self._coerce_points(test_points, prefix="test")
        norm_patterns = self._normalize_patterns(patterns)
        sentinel = self._coerce_point(sentinel_point, point_id="sentinel") if sentinel_point is not None else None
        interval = int(sentinel_every_n_runs) if sentinel_every_n_runs else None
        if interval is not None and interval <= 0:
            raise ValueError("sentinel_every_n_runs must be > 0 when provided.")

        base_specs = self._build_base_specs(points, norm_patterns, phase=phase)
        metadata = dict(base_metadata or {})
        metadata.setdefault("phase", phase)

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
        run_records = list(records) if records is not None else list(self.records)
        th = dict(self.thresholds)
        if thresholds:
            th.update({str(k): float(v) for k, v in thresholds.items()})

        immediate_pairs = self._extract_immediate_pairs(run_records)
        bracketed_groups = self._extract_bracketed_groups(run_records)
        sentinel_df = self._extract_sentinel(run_records)

        immediate_abs_devs = immediate_pairs["abs_deviation"].to_numpy(dtype=float) if not immediate_pairs.empty else np.array([])
        immediate_rsds = immediate_pairs["pair_rsd_pct"].to_numpy(dtype=float) if not immediate_pairs.empty else np.array([])
        immediate_diffs = immediate_pairs["diff"].to_numpy(dtype=float) if not immediate_pairs.empty else np.array([])

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
            "immediate_rsd_pct": _median_or_nan(immediate_rsds),
            "immediate_abs_deviation_median": _median_or_nan(immediate_abs_devs),
            "immediate_noise_sigma": noise_sigma,
            "immediate_averaged_sigma": averaged_sigma,
            "memory_abs_shift_median": _median_or_nan(memory_abs),
            "sentinel_drift_slope": sentinel_slope,
            "sentinel_drift_slope_abs": sentinel_slope_abs,
        }

        decision_raw, reasons = decide(metrics, thresholds=th)
        decision = DecisionLabel(decision_raw)
        drift_detected = bool(np.isfinite(sentinel_slope_abs) and sentinel_slope_abs > th["drift_pass_slope"])

        total = len(run_records)
        successful = sum(1 for r in run_records if r.result.success and np.isfinite(r.result.objective))

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
                "immediate_pairs": int(len(immediate_pairs)),
                "bracketed_groups": int(len(bracketed_groups)),
                "sentinel_runs": int(len(sentinel_df)),
            },
        }

    def escalate_cleaning_and_retest(
        self,
        selected_points: Sequence[Point | Mapping[str, float]] | None = None,
        sentinel_point: Point | Mapping[str, float] | None = None,
        sentinel_every_n_runs: int | None = None,
        patterns: Sequence[ReplicatePattern | str] | None = None,
        thresholds: Mapping[str, float] | None = None,
        max_cleaning_level: int = 3,
        base_metadata: Mapping[str, Any] | None = None,
        cleaning_callback: Callable[[int], None] | None = None,
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
                sentinel_point=sentinel_point,
                sentinel_every_n_runs=sentinel_every_n_runs,
                base_metadata=run_meta,
                phase=f"cleaning_{self.cleaning_level}",
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
