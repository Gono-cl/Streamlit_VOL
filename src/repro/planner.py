"""Design-space point planners used by reproducibility studies."""

from __future__ import annotations

from itertools import product
from typing import Mapping, Sequence

import numpy as np
from core.campaigns.variables import clip_point, coerce_value, is_categorical
from core.optimization.initial_design import lhs_samples


Bounds = Mapping[str, tuple[float, float] | list[str]]


def _validate_bounds(bounds: Bounds) -> list[str]:
    names = list(bounds.keys())
    if not names:
        raise ValueError("Bounds cannot be empty.")
    for name in names:
        if is_categorical(bounds[name]):
            if not bounds[name]:
                raise ValueError(f"Categories for '{name}' cannot be empty.")
            continue
        low, high = bounds[name]
        low_f = float(low)
        high_f = float(high)
        if not np.isfinite(low_f) or not np.isfinite(high_f):
            raise ValueError(f"Bounds for '{name}' must be finite.")
        if low_f > high_f:
            raise ValueError(f"Lower bound must be <= upper bound for '{name}'.")
    return names


def _downsample_evenly(points: list[dict[str, float | str]], n: int) -> list[dict[str, float | str]]:
    if n <= 0:
        return []
    if n >= len(points):
        return points
    idx = np.linspace(0, len(points) - 1, n, dtype=int)
    return [points[int(i)] for i in idx.tolist()]


def points_from_user(user_points: Sequence[Mapping[str, float | str]], bounds: Bounds | None = None) -> list[dict[str, float | str]]:
    """Validate and normalize user-provided points."""
    out: list[dict[str, float | str]] = []
    names = list(bounds.keys()) if bounds is not None else None
    for raw in user_points:
        point = {str(k): v for k, v in raw.items()}
        if names is not None:
            missing = [k for k in names if k not in point]
            if missing:
                raise ValueError(f"User point missing keys: {missing}")
            values = [coerce_value(point[name], bounds[name]) for name in names]
            point = dict(zip(names, clip_point(values, list(bounds.values()))))
        out.append(point)
    return out


def corners_and_center(
    bounds: Bounds,
    include_center: bool = True,
    max_points: int | None = None,
) -> list[dict[str, float | str]]:
    """
    Generate numeric corners crossed with categorical levels, plus a reference point.

    For d variables, corners generate 2^d points.
    """
    names = _validate_bounds(bounds)
    choices = [bounds[name] if is_categorical(bounds[name]) else (bounds[name][0], bounds[name][1])
               for name in names]
    points = [dict(zip(names, values)) for values in product(*choices)]
    if include_center:
        center = {name: domain[0] if is_categorical(domain) else (domain[0] + domain[1]) / 2.0
                  for name, domain in bounds.items()}
        points.append(center)

    if max_points is not None:
        points = _downsample_evenly(points, int(max_points))
    return points


def latin_hypercube(
    bounds: Bounds,
    n_points: int,
    seed: int = 42,
) -> list[dict[str, float | str]]:
    """Simple Latin hypercube sampling implementation using NumPy only."""
    names = _validate_bounds(bounds)
    n = int(n_points)
    if n <= 0:
        return []

    rng = np.random.default_rng(seed)
    samples = lhs_samples(list(bounds.values()), n, rng)
    return [dict(zip(names, point)) for point in samples]


def build_initialization_points(
    bounds: Bounds,
    n_points: int,
    method: str = "corners_center",
    seed: int = 42,
    user_points: Sequence[Mapping[str, float | str]] | None = None,
    include_center: bool = True,
) -> list[dict[str, float | str]]:
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
