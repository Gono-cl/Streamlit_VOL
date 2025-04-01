from skopt import Optimizer

class StepBayesianOptimizer:
    def __init__(self, variables, base_estimator="GP", acq_func="EI", random_state=42):
        self.variable_names = [name for name, *_ in variables]
        self.space = [(low, high) for _, low, high in variables]
        self._optimizer = Optimizer(
            dimensions=self.space,
            base_estimator=base_estimator,
            acq_func=acq_func,
            random_state=random_state
        )
        self.x_iters = []
        self.y_iters = []

    def suggest(self):
        return self._optimizer.ask()

    def observe(self, x, y):
        self._optimizer.tell(x, y)
        self.x_iters.append(x)
        self.y_iters.append(y)

    @property
    def skopt_optimizer(self):
        return self._optimizer


