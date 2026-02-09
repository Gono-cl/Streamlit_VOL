"""Shared initial design generators for optimization campaigns."""

from __future__ import annotations

import numpy as np


def lhs_samples(bounds, n, rng):
    d = len(bounds)
    if d == 0 or n <= 0:
        return []
    lhs = np.zeros((n, d))
    for j in range(d):
        perm = rng.permutation(n)
        lhs[:, j] = (perm + rng.random(n)) / n
    for j, (low, high) in enumerate(bounds):
        lhs[:, j] = low + lhs[:, j] * (high - low)
    return [lhs[i, :].tolist() for i in range(n)]


def normalize_points(points, bounds):
    norm = []
    for x in points:
        nx = []
        for (val, (low, high)) in zip(x, bounds):
            rng = high - low
            if rng > 0:
                nx.append((float(val) - low) / rng)
            else:
                nx.append(0.5)
        norm.append(nx)
    return np.array(norm) if norm else np.empty((0, len(bounds)))


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
        pool_real = [[rng.uniform(low, high) for (low, high) in bounds] for _ in range(pool_n)]
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

    def _to_real(U):
        R = np.zeros_like(U)
        for j, (low, high) in enumerate(bounds):
            R[:, j] = low + U[:, j] * (high - low)
        return R

    reused_norm = normalize_points(reused_points, bounds)

    best_score = -np.inf
    best_new_real = []

    for _ in range(max(1, trials)):
        U = _lhs_unit(m)
        unused = set(range(m))
        for rp in reused_norm:
            if not unused:
                break
            un_idx = np.array(sorted(list(unused)))
            diff = U[un_idx] - rp
            d2 = np.einsum("ij,ij->i", diff, diff)
            j = int(un_idx[int(np.argmin(d2))])
            unused.remove(j)

        if not unused and r < m:
            continue
        new_rows_U = U[list(sorted(unused))]
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
            best_new_real = _to_real(new_rows_U).tolist()

    return best_new_real
