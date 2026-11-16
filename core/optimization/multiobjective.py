"""Construct ProcessOptimizer backends for numeric and categorical campaigns."""

from collections import Counter

from ProcessOptimizer import Optimizer
from ProcessOptimizer.space import Categorical


class CategoricalMultiObjectiveOptimizer(Optimizer):
    """Support category-only spaces, which ProcessOptimizer's GP rejects."""

    def __init__(self, dimensions, **kwargs):
        kwargs.setdefault("base_estimator", "RF")
        super().__init__(dimensions, **kwargs)

    def stbr_scipy(self, n_points=1):
        # The native exploration routine creates a GP-only copy. Balance visits
        # to valid categorical combinations instead of optimizing numeric gaps.
        candidates = self.space.rvs(n_samples=max(128, n_points * 32), random_state=self.rng)
        visits = Counter(tuple(point) for point in self.Xi)
        selected = []
        for _ in range(n_points):
            point = min(candidates, key=lambda candidate: visits[tuple(candidate)])
            selected.append(list(point))
            visits[tuple(point)] += 1
        return selected


def build_multiobjective_optimizer(dimensions, **kwargs):
    backend = CategoricalMultiObjectiveOptimizer if all(isinstance(d, Categorical) for d in dimensions) else Optimizer
    return backend(dimensions=dimensions, **kwargs)
