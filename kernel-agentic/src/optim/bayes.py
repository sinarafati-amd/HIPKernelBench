from __future__ import annotations
import optuna, random
from typing import Dict, Any

_SPACE = {
    # param_name     (low, high, step or list)
    "block_size"  : (64, 1024, 32),          # multiples of 32
    "vector_width": [1,2,4,8],               # how many elems per lane
    "tile_m"      : (8, 64, 8),              # shared-mem tile sizes
    "tile_n"      : (8, 64, 8),
}

def _suggest(trial: optuna.trial.Trial) -> Dict[str, Any]:
    cfg = {}
    for k,v in _SPACE.items():
        if isinstance(v, tuple):
            lo, hi, step = v
            cfg[k] = trial.suggest_int(k, lo, hi, step=step)
        else:
            cfg[k] = trial.suggest_categorical(k, v)
    return cfg

class BayesOpt:
    def __init__(self, max_trials: int, space: Dict[str, Any], seed: int = 0):
        self.space      = space
        self.study      = optuna.create_study(direction="maximize",
                                              sampler=optuna.samplers.TPESampler(seed=seed))
        self.max_trials = max_trials

    def next_params(self) -> Dict[str, Any] | None:
        if len(self.study.trials) >= self.max_trials:
            return None
        trial = self.study.ask()
        # now sample _only_ from self.space
        params = {}
        for k,v in self.space.items():
            if isinstance(v, tuple):
                lo,hi,step = v
                params[k] = trial.suggest_int(k, lo, hi, step=step)
            else:
                params[k] = trial.suggest_categorical(k, v)
        return {"_trial": trial, **params}

    def update(self, handle, speedup: float):
        self.study.tell(handle, speedup)
