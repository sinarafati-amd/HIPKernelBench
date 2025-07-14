import torch, ctypes, pathlib, inspect, runpy
from types import ModuleType
from typing import Callable

def _load_torch_fn(torch_file: str) -> Callable[[torch.Tensor], torch.Tensor]:
    """
    Execute the torch file and return either:
    * run_ref() if present, else
    * the single *_ref function detected.
    """
    ns: dict = {}
    exec(pathlib.Path(torch_file).read_text(), ns)
    if "run_ref" in ns and callable(ns["run_ref"]):
        return ns["run_ref"]
    ref_fns = [v for k,v in ns.items() if callable(v) and k.endswith("_ref")]
    if len(ref_fns) != 1:
        raise RuntimeError("Need a single reference fn ending with _ref")
    return ref_fns[0]

def hip_forward(so_path: str, x: torch.Tensor) -> torch.Tensor:
    lib = ctypes.CDLL(so_path)
    lib.run_kernel.argtypes  = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
    lib.run_kernel.restype   = None
    y = torch.empty_like(x)
    lib.run_kernel(
        ctypes.c_void_p(x.data_ptr()),
        ctypes.c_void_p(y.data_ptr()),
        ctypes.c_int(x.numel())
    )
    torch.cuda.synchronize()
    return y

def max_abs_err(torch_file: str, so_path: str, shape=(1024,)) -> float:
    ref_fn = _load_torch_fn(torch_file)
    x = torch.randn(*shape, device="cuda", dtype=torch.float32)
    y_ref = ref_fn(x)
    y_hip = hip_forward(so_path, x)
    return (y_ref - y_hip).abs().max().item()
