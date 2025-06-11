import time, torch

def time_fn(fn, n_trial=30):
    torch.cuda.synchronize()
    t0 = time.perf_counter()
    for _ in range(n_trial): fn()
    torch.cuda.synchronize(); t1 = time.perf_counter()
    return (t1 - t0) / n_trial
