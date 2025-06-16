import json
import pathlib
import time
import inspect
from typing import Any, Dict, Callable, List
from tqdm import tqdm
import torch
import torch.nn as nn


def _baseline_latency(torch_file: str, n_trial: int = 30) -> float:


    # 1) check cache
    stem = pathlib.Path(torch_file).stem
    cache_file = pathlib.Path("logs") / stem / f"baseline_{stem}.json"
    if cache_file.exists():
        return json.load(open(cache_file))["lat_us"]

    # 2) load the file into a namespace
    namespace: Dict[str, Any] = {}
    code = pathlib.Path(torch_file).read_text()
    exec(compile(code, torch_file, 'exec'), namespace)

    # 3) pick your CUDA device once
    device_idx = torch.cuda.current_device()
    DEVICE = torch.device(f"cuda:{device_idx}")

    # 4) build run_fn
    if callable(namespace.get("run_ref")):
        run_fn: Callable[[], Any] = namespace["run_ref"]

    elif callable(namespace.get("get_inputs")):
        get_inputs = namespace["get_inputs"]
        get_init = namespace.get("get_init_inputs", lambda: [])

        # find exactly one nn.Module subclass
        mods = [
            obj for obj in namespace.values()
            if isinstance(obj, type) and issubclass(obj, nn.Module)
        ]
        if len(mods) != 1:
            raise RuntimeError(
                f"{torch_file}: expected exactly one nn.Module subclass, found {len(mods)}"
            )
        ModelClass = mods[0]
        breakpoint()
        init_args = get_init()
        if not isinstance(init_args, (list, tuple)):
            raise RuntimeError("get_init_inputs() must return a list or tuple")

        # instantiate + move model
        model = ModelClass(*init_args).to(DEVICE).eval()

        # generate + move inputs once
        cpu_inputs = get_inputs()
        if not isinstance(cpu_inputs, (list, tuple)):
            raise RuntimeError("get_inputs() must return a list or tuple of tensors")
        inputs_cuda = [inp.to(DEVICE, non_blocking=True) for inp in cpu_inputs]

        def run_fn():
            return model(*inputs_cuda)

    else:
        # fallback: exactly one fn ending in _ref
        ref_fns = [
            v for k, v in namespace.items()
            if callable(v) and k.endswith("_ref")
        ]
        if len(ref_fns) != 1:
            raise RuntimeError(
                f"{torch_file}: must define run_ref(), get_inputs(), or exactly one *_ref; found {len(ref_fns)}"
            )
        fn = ref_fns[0]
        sig = inspect.signature(fn)

        def run_fn():
            args: List[torch.Tensor] = []
            for p in sig.parameters.values():
                if "weight" in p.name:
                    args.append(
                        torch.randn(8, 3, 3, 3, device=DEVICE, dtype=torch.float16)
                    )
                elif "bias" in p.name:
                    args.append(
                        torch.randn(8, device=DEVICE, dtype=torch.float16)
                    )
                else:
                    args.append(
                        torch.randn(1, 3, 32, 32, device=DEVICE, dtype=torch.float16)
                    )
            return fn(*args)

    # 5) warm-up (so first kernel launch isn’t in measurements)
    with torch.no_grad():
        run_fn()
        torch.cuda.synchronize()

    # 6) timing loop with CUDA events
    start_evt = torch.cuda.Event(enable_timing=True)
    end_evt   = torch.cuda.Event(enable_timing=True)
    timings_ms: List[float] = []

    with torch.no_grad():
        for _ in tqdm(range(n_trial), desc="Benchmarking"):
            start_evt.record()
            run_fn()
            end_evt.record()
            torch.cuda.synchronize()
            # elapsed_time gives milliseconds
            timings_ms.append(start_evt.elapsed_time(end_evt))

    # 7) compute average & convert to micro-seconds
    avg_ms = sum(timings_ms) / len(timings_ms)
    lat_us = avg_ms * 1000.0

    # 8) cache & return
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_file, "w") as f:
        json.dump({"lat_us": lat_us}, f)

    print(f"[Device: {DEVICE}] Avg elapsed: {avg_ms:.2f} ms")
    return lat_us
