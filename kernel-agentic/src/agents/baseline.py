import json, pathlib, inspect, time, typing, contextlib, sys
from typing import Any, Dict, Callable, List
import torch, torch.nn as nn
import os 

os.environ.setdefault("MIOPEN_ENABLE_GPUKERN_DEBUG", "0")  # faster compile
os.environ.setdefault("MIOPEN_USER_DB_PATH",  "/tmp/miopen_kcache")


def _baseline_latency(
    torch_file: str,
    n_trial: int = 30,
    warmup: int = 1,
    regenerate_inputs: bool = False,
) -> float:
    """
    Measure (and cache) the reference latency of code in `torch_file` on CUDA.

    Returns average latency in **micro-seconds**.
    """
    
    stem = pathlib.Path(torch_file).stem
    cache_path = pathlib.Path("logs") / stem / f"baseline_{stem}.json"

    # --------------------------------------------------------------------- cache
    if cache_path.exists():
        return json.load(open(cache_path))["lat_us"]

    # ------------------------------------------------------------------ load code
    ns: Dict[str, Any] = {}
    exec(compile(pathlib.Path(torch_file).read_text(), torch_file, "exec"), ns)

    # ------------------------------------------------------------------ build run_fn
    if callable(ns.get("run_ref")):
        run_fn = ns["run_ref"]

    elif callable(ns.get("get_inputs")):                     # path 3-b
        get_inputs = ns["get_inputs"]
        get_init = ns.get("get_init_inputs", lambda: [])
        mods = [v for v in ns.values()
                if isinstance(v, type) and issubclass(v, nn.Module)]
        if len(mods) != 1:
            raise RuntimeError(f"{torch_file}: expected exactly one nn.Module subclass")

        Model = mods[0]
        # model = Model(*get_init()).to("cuda").eval()
        model = Model(*get_init()).eval().to("cuda")
        torch.backends.cudnn.benchmark = True

        # materialise inputs once so they live on GPU
        prototype_inputs = [x.to("cuda") for x in get_inputs()]

        def run_fn():
            if regenerate_inputs:
                inputs = [x.to("cuda") for x in get_inputs()]
            else:
                inputs = prototype_inputs
            return model(*inputs)

    else:                                                   # path 3-c
        ref_fns = [v for k, v in ns.items()
                   if callable(v) and k.endswith("_ref")]
        if len(ref_fns) != 1:
            raise RuntimeError(f"{torch_file}: no run_ref(), get_inputs() or single *_ref")
        fn = ref_fns[0]

        sig = inspect.signature(fn)
        # build synthetic, **GPU-resident** args once
        sample_args: List[torch.Tensor] = []
        for p in sig.parameters.values():
            if "weight" in p.name:
                sample_args.append(torch.randn(8, 3, 3, 3, device="cuda", dtype=torch.float16))
            elif "bias" in p.name:
                sample_args.append(torch.randn(8, device="cuda", dtype=torch.float16))
            else:
                sample_args.append(torch.randn(1, 3, 32, 32, device="cuda", dtype=torch.float16))

        def run_fn():
            return fn(*sample_args)

    # --------------------------------------------------------- timing helpers
    starter, ender = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)

    def time_once() -> float:                                # returns micro-seconds
        starter.record()
        run_fn()
        ender.record()
        torch.cuda.synchronize()
        return starter.elapsed_time(ender) * 1e3             # ms → µs

    # ---------------------------------------------------------------- warm-up
    for _ in range(warmup):
        time_once()

    # ------------------------------------------------------------- main timing
    timings = []
    print(f"Timing {stem}: ", end="", flush=True)
    for i in range(n_trial):
        lat = time_once()
        timings.append(lat)
        print(".", end="", flush=True)
    print()

    lat_us = sum(timings) / len(timings)

    # ---------------------------------------------------------------- cache
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    json.dump({"lat_us": lat_us}, open(cache_path, "w"))

    return lat_us
