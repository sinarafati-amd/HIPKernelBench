import json
import pathlib
import time
import inspect
from typing import Any, Dict, Callable, List

import torch
import torch.nn as nn

def _baseline_latency(torch_file: str, n_trial: int = 30) -> float:
    """
    Measure and cache the reference PyTorch latency in micro-seconds.

    • Caches results under `logs/baseline_<stem>.json`.
    • First looks for a callable `run_ref()`.
    • Next, if there's a callable `get_inputs()`, it:
        – Finds exactly one nn.Module subclass in the file.
        – Instantiates it using get_init_inputs() if present.
        – Wraps the model and get_inputs into a `run_fn`.
    • Otherwise, auto-detects a single function ending in `_ref`
      and fabricates dummy tensors based on parameter names.
    """
    # 1) check cache
    stem = pathlib.Path(torch_file).stem

    cache_file = pathlib.Path("logs")/stem / f"baseline_{stem}.json"
    if cache_file.exists():
        return json.load(open(cache_file))["lat_us"]

    # 2) load the file into a namespace
    namespace: Dict[str, Any] = {}
    code = pathlib.Path(torch_file).read_text()
    exec(compile(code, torch_file, 'exec'), namespace)

    # 3) pick your runner
    run_fn: Callable[[], Any]

    # 3a) user-supplied run_ref()
    if callable(namespace.get("run_ref", None)):
        run_fn = namespace["run_ref"]

    # 3b) user-supplied get_inputs() + (opt) get_init_inputs()
    elif callable(namespace.get("get_inputs", None)):
        get_inputs = namespace["get_inputs"]
        get_init = namespace.get("get_init_inputs", lambda: [])

        # find exactly one Module subclass
        mods = [
            obj for obj in namespace.values()
            if isinstance(obj, type) and issubclass(obj, nn.Module)
        ]
        if len(mods) != 1:
            raise RuntimeError(
                f"{torch_file}: expected exactly one nn.Module subclass, found {len(mods)}"
            )
        ModelClass = mods[0]
        init_args = get_init()
        if not isinstance(init_args, (list, tuple)):
            raise RuntimeError("get_init_inputs() must return a list or tuple")

        model = ModelClass(*init_args).to("cuda").eval()

        def run_fn():
            inputs = get_inputs()
            if not isinstance(inputs, (list, tuple)):
                raise RuntimeError("get_inputs() must return a list or tuple of tensors")
            # move everything to CUDA
            inputs_cuda: List[torch.Tensor] = [inp.to("cuda") for inp in inputs]
            return model(*inputs_cuda)

    # 3c) fallback: exactly one fn ending in _ref
    else:
        ref_fns = [
            v for k, v in namespace.items()
            if callable(v) and k.endswith("_ref")
        ]
        if len(ref_fns) != 1:
            raise RuntimeError(
                f"{torch_file}: must define run_ref(), get_inputs(), or exactly one *_ref; found {len(ref_fns)}"
            )
        fn = ref_fns[0]

        def run_fn():
            sig = inspect.signature(fn)
            args: List[torch.Tensor] = []
            for p in sig.parameters.values():
                # very crude tensor heuristics—feel free to tune!
                if "weight" in p.name:
                    args.append(
                        torch.randn(8, 3, 3, 3, device="cuda", dtype=torch.float16)
                    )
                elif "bias" in p.name:
                    args.append(
                        torch.randn(8, device="cuda", dtype=torch.float16)
                    )
                else:
                    args.append(
                        torch.randn(1, 3, 32, 32, device="cuda", dtype=torch.float16)
                    )
            return fn(*args)

    # 4) timing loop
    torch.cuda.synchronize()
    with torch.no_grad():
        timings = []
        for _ in range(n_trial):
            start = time.time()
            run_fn()
            torch.cuda.synchronize()
            timings.append(time.time() - start)
    avg_s = sum(timings) / n_trial

    # 5) cache & return
    lat_us = avg_s * 1e6
    cache_file.parent.mkdir(exist_ok=True)
    with open(cache_file, "w") as f:
        json.dump({"lat_us": lat_us}, f)
    return lat_us



