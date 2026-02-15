"""Design-space point planners used by reproducibility studies."""

from __future__ import annotations

from itertools import product
from typing import Mapping, Sequence

import numpy as np


Bounds = Mapping[str, tuple[float, float]]


def _validate_bounds(bounds: Bounds) -> list[str]:
    names = list(bounds.keys())
    if not names:
        raise ValueError("Bounds cannot be empty.")
    for name in names:
        low, high = bounds[name]
        low_f = float(low)
        high_f = float(high)
        if not np.isfinite(low_f) or not np.isfinite(high_f):
            raise ValueError(f"Bounds for '{name}' must be finite.")
        if low_f > high_f:
            raise ValueError(f"Lower bound must be <= upper bound for '{name}'.")
    return names


def _downsample_evenly(points: list[dict[str, float]], n: int) -> list[dict[str, float]]:
    if n <= 0:
        return []
    if n >= len(points):
        return points
    idx = np.linspace(0, len(points) - 1, n, dtype=int)
    return [points[int(i)] for i in idx.tolist()]


def points_from_user(user_points: Sequence[Mapping[str, float]], bounds: Bounds | None = None) -> list[dict[str, float]]:
    """Validate and normalize user-provided points."""
    out: list[dict[str, float]] = []
    names = list(bounds.keys()) if bounds is not None else None
    for raw in user_points:
        point = {str(k): float(v) for k, v in raw.items()}
        if names is not None:
            missing = [k for k in names if k not in point]
            if missing:
                raise ValueError(f"User point missing keys: {missing}")
            clipped: dict[str, float] = {}
            for name in names:
                low, high = bounds[name]
                val = float(point[name])
                clipped[name] = min(max(val, float(low)), float(high))
            point = clipped
        out.append(point)
    return out


def corners_and_center(
    bounds: Bounds,
    include_center: bool = True,
    max_points: int | None = None,
) -> list[dict[str, float]]:
    """
    Generate corner points and optional center for numeric bounds.

    For d variables, corners generate 2^d points.
    """
    names = _validate_bounds(bounds)
    corner_rows: list[dict[str, float]] = []
    for bitmask in product([0, 1], repeat=len(names)):
        row: dict[str, float] = {}
        for bit, name in zip(bitmask, names):
            low, high = bounds[name]
            row[name] = float(high if bit else low)
        corner_rows.append(row)

    points = list(corner_rows)
    if include_center:
        center: dict[str, float] = {}
        for name in names:
            low, high = bounds[name]
            center[name] = (float(low) + float(high)) / 2.0
        points.append(center)

    if max_points is not None:
        points = _downsample_evenly(points, int(max_points))
    return points


def latin_hypercube(
    bounds: Bounds,
    n_points: int,
    seed: int = 42,
) -> list[dict[str, float]]:
    """Simple Latin hypercube sampling implementation using NumPy only."""
    names = _validate_bounds(bounds)
    n = int(n_points)
    if n <= 0:
        return []

    rng = np.random.default_rng(seed)
    samples = np.zeros((n, len(names)), dtype=float)
    for j in range(len(names)):
        perm = rng.permutation(n)
        samples[:, j] = (perm + rng.random(n)) / n

    out: list[dict[str, float]] = []
    for i in range(n):
        row: dict[str, float] = {}
        for j, name in enumerate(names):
            low, high = bounds[name]
            row[name] = float(low + samples[i, j] * (float(high) - float(low)))
        out.append(row)
    return out


def build_initialization_points(
    bounds: Bounds,
    n_points: int,
    method: str = "corners_center",
    seed: int = 42,
    user_points: Sequence[Mapping[str, float]] | None = None,
    include_center: bool = True,
) -> list[dict[str, float]]:
    """
    Build initialization points from planner method plus optional user points.

    Supported methods: `corners_center`, `lhs`.
    """
    method_key = method.strip().lower()
    if method_key == "corners_center":
        planned = corners_and_center(bounds, include_center=include_center, max_points=n_points)
    elif method_key in {"lhs", "latin_hypercube"}:
        planned = latin_hypercube(bounds, n_points=n_points, seed=seed)
    else:
        raise ValueError(f"Unknown initialization method '{method}'.")

    extras = points_from_user(user_points or [], bounds=bounds)
    merged = list(planned)
    for candidate in extras:
        if candidate not in merged:
            merged.append(candidate)
    return merged
