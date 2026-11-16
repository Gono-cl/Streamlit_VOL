"""Shared initial design generators for optimization campaigns."""

from __future__ import annotations

import numpy as np

from core.campaigns.variables import coerce_value, is_categorical


def lhs_samples(bounds, n, rng):
    d = len(bounds)
    if d == 0 or n <= 0:
        return []
    lhs = np.zeros((n, d))
    for j in range(d):
        perm = rng.permutation(n)
        lhs[:, j] = (perm + rng.random(n)) / n
    return _unit_points(lhs, bounds)


def _unit_points(unit_points, bounds):
    points = []
    for row in unit_points:
        point = []
        for value, domain in zip(row, bounds):
            if is_categorical(domain):
                point.append(domain[min(int(value * len(domain)), len(domain) - 1)])
            else:
                point.append(domain[0] + value * (domain[1] - domain[0]))
        points.append(point)
    return points


def normalize_points(points, bounds):
    """Continuous scaling plus equal-distance one-hot categorical coordinates."""
    norm = []
    for point in points:
        normalized = []
        for value, domain in zip(point, bounds):
            if is_categorical(domain):
                # Reused observations can contain levels outside the campaign.
                # They have equal distance to all levels currently allowed.
                try:
                    value = coerce_value(value, domain)
                except ValueError:
                    value = None
                normalized.extend([float(value == level) / np.sqrt(2.0) for level in domain])
            else:
                value = coerce_value(value, domain)
                low, high = domain
                normalized.append((value - low) / (high - low) if high > low else 0.5)
        norm.append(normalized)
    width = sum(len(d) if is_categorical(d) else 1 for d in bounds)
    return np.array(norm) if norm else np.empty((0, width))


def gap_aware_initial_points(bounds, n, rng, existing_points=None, method="LHS", pool_factor=10):
    """
    Generate n initial points that are well spread relative to existing_points using
    farthest-point sampling from a larger random or LHS candidate pool.
    """
    if n <= 0:
        return []

    pool_n = max(n * max(2, int(pool_factor)), n)
    if method == "LHS":
        pool_real = lhs_samples(bounds, pool_n, rng)
    else:
        pool_real = [[domain[int(rng.integers(len(domain)))] if is_categorical(domain)
                      else rng.uniform(*domain) for domain in bounds] for _ in range(pool_n)]
    pool_norm = normalize_points(pool_real, bounds)

    existing_points = existing_points or []
    exist_norm = normalize_points(existing_points, bounds)

    selected = []
    if pool_norm.size == 0:
        return selected

    if exist_norm.shape[0] > 0:
        min_d2 = np.full(pool_norm.shape[0], np.inf)
        for e in exist_norm:
            diff = pool_norm - e
            d2 = np.einsum("ij,ij->i", diff, diff)
            min_d2 = np.minimum(min_d2, d2)
    else:
        min_d2 = np.full(pool_norm.shape[0], np.inf)

    taken = np.zeros(pool_norm.shape[0], dtype=bool)
    for _ in range(n):
        avail = ~taken
        if not np.any(avail):
            break
        idx = int(np.argmax(np.where(avail, min_d2, -1)))
        taken[idx] = True
        selected.append(pool_real[idx])
        p = pool_norm[idx]
        diff = pool_norm - p
        d2 = np.einsum("ij,ij->i", diff, diff)
        min_d2 = np.minimum(min_d2, d2)
    return selected


def augmented_lhs_with_reuse(bounds, total_points, rng, reused_points=None, trials=30):
    """
    Build a Latin hypercube of size total_points and align it to reused points.
    Return only the remaining rows as new points and pick the best trial by
    maximizing the minimum pairwise distance in normalized space.
    """
    d = len(bounds)
    m = int(total_points)
    if m <= 0:
        return []
    reused_points = reused_points or []
    r = len(reused_points)
    if r >= m:
        return []

    def _lhs_unit(n):
        if n <= 0:
            return np.empty((0, d))
        M = np.zeros((n, d))
        for j in range(d):
            perm = rng.permutation(n)
            M[:, j] = (perm + rng.random(n)) / n
        return M

    reused_norm = normalize_points(reused_points, bounds)

    best_score = -np.inf
    best_new_real = []

    for _ in range(max(1, trials)):
        U = _lhs_unit(m)
        real_points = _unit_points(U, bounds)
        normalized = normalize_points(real_points, bounds)
        unused = set(range(m))
        for rp in reused_norm:
            if not unused:
                break
            un_idx = np.array(sorted(list(unused)))
            diff = normalized[un_idx] - rp
            d2 = np.einsum("ij,ij->i", diff, diff)
            j = int(un_idx[int(np.argmin(d2))])
            unused.remove(j)

        if not unused and r < m:
            continue
        new_rows_U = normalized[list(sorted(unused))]
        union = new_rows_U
        if reused_norm.shape[0] > 0:
            union = np.vstack([reused_norm, new_rows_U])
        if union.shape[0] > 1:
            mn = np.inf
            for i in range(union.shape[0]):
                d2 = np.einsum("ij,ij->i", (union - union[i]), (union - union[i]))
                d2[i] = np.inf
                mn = min(mn, float(np.min(d2)))
            score = mn
        else:
            score = 0.0

        if score > best_score:
            best_score = score
            best_new_real = [real_points[i] for i in sorted(unused)]

    return best_new_real
