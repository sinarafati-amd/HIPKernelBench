import optuna, subprocess, json, tempfile, os
from ..utils.occupancy import suggest_launch

def objective(trial):
    tile     = trial.suggest_categorical("tile", [8,16,32])
    vecload  = trial.suggest_int("vec", 1, 4)
    tpb      = trial.suggest_categorical("tpb", [64,128,256])

    launch   = suggest_launch(tile*tile*4, tpb)
    # Generate kernel by templating launch params into HIPGenerator prompt …
    code     = generate_kernel(tile, vecload, launch)
    stats    = compile_and_profile(code)
    return stats["avg_us"]   # minimise latency

optuna.study.create_study(direction="minimize").optimize(objective, n_trials=30)