import torch, importlib.util, tempfile, subprocess, os, yaml
from ..utils.gpu_specs import get_gpu_specs

def load_kernel(bin_path):
    # assumes host stub exposes run_kernel(...)
    spec = importlib.util.spec_from_file_location("hip_mod", bin_path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.run_kernel

def is_correct(torch_fn, hip_bin, shape, dtype=torch.float16, atol=1e-2):
    x = torch.randn(*shape, device="cuda", dtype=dtype)
    torch_out = torch_fn(x)
    hip_out   = torch.empty_like(torch_out)
    load_kernel(hip_bin)(x, hip_out)
    return torch.allclose(torch_out, hip_out, atol=atol)
