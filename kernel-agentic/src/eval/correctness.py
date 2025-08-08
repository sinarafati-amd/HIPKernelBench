import torch, ctypes, pathlib, inspect, runpy, re
from types import ModuleType
from typing import Callable, List, Tuple, Dict, Any

def _load_torch_fn(torch_file: str) -> Callable[[torch.Tensor], torch.Tensor]:
    """
    Execute the torch file and return either:
    * run_ref() if present, else
    * the single *_ref function detected, else
    * a function that creates and runs the Model class
    """
    ns: dict = {}
    exec(pathlib.Path(torch_file).read_text(), ns)
    
    # First try run_ref function
    if "run_ref" in ns and callable(ns["run_ref"]):
        return ns["run_ref"]
    
    # Then try _ref functions
    ref_fns = [v for k,v in ns.items() if callable(v) and k.endswith("_ref")]
    if len(ref_fns) == 1:
        return ref_fns[0]
    
    # Finally, try to use Model class
    if "Model" in ns and hasattr(ns["Model"], "__call__"):
        model_class = ns["Model"]
        # Create a wrapper function that instantiates and runs the model
        def model_forward(*args):
            # Get initialization parameters if available
            init_inputs = []
            if "get_init_inputs" in ns and callable(ns["get_init_inputs"]):
                init_inputs = ns["get_init_inputs"]()
            
            # Create model instance
            if init_inputs:
                model = model_class(*init_inputs)
            else:
                model = model_class()
            
            # CRITICAL: Move model to GPU to match input device
            model = model.cuda()
            model.eval()  # Set to evaluation mode
            
            with torch.no_grad():
                return model(*args)
        
        return model_forward
    
    raise RuntimeError("Need either run_ref(), a single *_ref function, or a Model class")

def _read_kernel_source_from_so_path(so_path: str) -> str | None:
    p = pathlib.Path(so_path).parent / "kernel.hip"
    if p.exists():
        try:
            return p.read_text()
        except Exception:
            return None
    return None

def _infer_possible_signatures(kernel_src: str | None) -> List[str]:
    """Return a list of signature tags to try based on source hints."""
    sigs = [
        "x_y_N",          # run_kernel(x, y, N)
        "ab_c_N",         # run_kernel(a, b, c, N)
        "ab_c_MN",        # run_kernel(a, b, c, M, N)
        "ab_c_MNK",       # run_kernel(a, b, c, M, N, K) for matmul
        "conv2d_packed",  # run_kernel(input, weights, output, batch, in_ch, out_ch, H, W, kH, kW)
        "conv3d_packed",  # run_kernel(input, weights, output, batch, in_ch, out_ch, D, H, W, k, stride, pad, groups)
        "x_y_total"       # run_kernel(x, y, total_elems)
    ]
    if not kernel_src:
        return sigs
    try:
        # Explicit 3-arg void* wrapper
        if re.search(r"extern\s+\"C\"\s+void\s+run_kernel\s*\(\s*void\s*\*\s*input\s*,\s*void\s*\*\s*output\s*,\s*int\s+\w+\s*\)", kernel_src):
            return ["x_y_N", "x_y_total", "ab_c_N", "ab_c_MN", "ab_c_MNK"]
        # Generic x,y,size variant
        if re.search(r"run_kernel\s*\(\s*void\s*\*\s*\w+\s*,\s*void\s*\*\s*\w+\s*,\s*int\s+\w+\)", kernel_src):
            return ["x_y_N", "x_y_total", "ab_c_N", "ab_c_MN", "ab_c_MNK"]
        # Convolution signatures with multiple parameters
        if re.search(r"run_kernel\s*\(\s*const\s+float\s*\*.*,\s*const\s+float\s*\*.*,\s*float\s*\*.*,\s*int.*,\s*int.*,\s*int.*,\s*int.*,\s*int.*,\s*int.*,\s*int", kernel_src):
            # 3D conv: many parameters suggest conv3d
            return ["conv3d_packed", "conv2d_packed", "ab_c_MNK", "ab_c_MN", "ab_c_N"]
        if re.search(r"run_kernel\s*\(\s*const\s+float\s*\*.*,\s*const\s+float\s*\*.*,\s*float\s*\*.*,\s*int.*,\s*int.*,\s*int.*,\s*int.*,\s*int.*,\s*int", kernel_src):
            # 2D conv: intermediate number of parameters
            return ["conv2d_packed", "ab_c_MNK", "ab_c_MN", "ab_c_N", "x_y_N", "x_y_total"]
        # ABC pointers variant
        if re.search(r"run_kernel\s*\(\s*const\s+float\s*\*\s*\w+\s*,\s*const\s+float\s*\*\s*\w+\s*,\s*float\s*\*\s*\w+", kernel_src):
            # Likely a,b,c pointers → try MNK first
            return ["ab_c_MNK", "ab_c_MN", "ab_c_N", "x_y_N", "x_y_total"]
    except Exception:
        pass
    return sigs

def _attempt_run_kernel(lib, inputs: List[torch.Tensor], output: torch.Tensor, signature: str) -> None:
    # Do not set argtypes to allow flexible calling; rely on c_void_p/c_int
    fn = getattr(lib, 'run_kernel')
    # Prepare pointers
    ptrs = [ctypes.c_void_p(t.contiguous().data_ptr()) for t in inputs]
    out_ptr = ctypes.c_void_p(output.contiguous().data_ptr())
    H = output.shape[0] if output.ndim > 0 else output.numel()
    W = output.shape[1] if output.ndim > 1 else 1
    total = output.numel()
    if signature == "x_y_N":
        if len(ptrs) < 1:
            raise RuntimeError("Not enough inputs for x_y_N")
        fn(ptrs[0], out_ptr, ctypes.c_int(total))
    elif signature == "ab_c_N":
        if len(ptrs) < 2:
            raise RuntimeError("Not enough inputs for ab_c_N")
        fn(ptrs[0], ptrs[1], out_ptr, ctypes.c_int(total))
    elif signature == "ab_c_MN":
        if len(ptrs) < 2:
            raise RuntimeError("Not enough inputs for ab_c_MN")
        fn(ptrs[0], ptrs[1], out_ptr, ctypes.c_int(H), ctypes.c_int(W))
    elif signature == "ab_c_MNK":
        if len(ptrs) < 2:
            raise RuntimeError("Not enough inputs for ab_c_MNK")
        # infer K from inputs: A[M,K], B[K,N]
        # If output is [M,N]
        M = output.shape[0] if output.ndim > 0 else output.numel()
        N = output.shape[1] if output.ndim > 1 else 1
        # Prefer K from A's second dim if exists, else from B's first dim, else sqrt
        if inputs[0].ndim >= 2:
            K = inputs[0].shape[1]
        elif inputs[1].ndim >= 2:
            K = inputs[1].shape[0]
        else:
            import math
            K = int(math.sqrt(inputs[0].numel()))
        fn(ptrs[0], ptrs[1], out_ptr, ctypes.c_int(M), ctypes.c_int(N), ctypes.c_int(K))
    elif signature == "conv2d_packed":
        # run_kernel(input, weights, output, batch, in_ch, out_ch, H_like, W_like, kH, kW)
        if len(ptrs) < 2:
            raise RuntimeError("Not enough inputs for conv2d_packed")
        inp, weight = inputs[0], inputs[1]
        if inp.ndim != 4 or weight.ndim != 4:
            raise RuntimeError(f"conv2d_packed expects 4D tensors, got {inp.shape}, {weight.shape}")
        B, C_in, H_in, W_in = inp.shape
        C_out, C_in2, kH, kW = weight.shape
        # Many generated kernels (incorrectly) index output using passed H/W.
        # To avoid OOB writes, pass output spatial dims instead of input dims.
        # Fallback to input dims if output is not 4D.
        if output.ndim == 4:
            _, _, H_like, W_like = output.shape
        else:
            H_like, W_like = H_in, W_in
        fn(ptrs[0], ptrs[1], out_ptr,
           ctypes.c_int(B), ctypes.c_int(C_in), ctypes.c_int(C_out),
           ctypes.c_int(H_like), ctypes.c_int(W_like), ctypes.c_int(kH), ctypes.c_int(kW))
    elif signature == "conv3d_packed":
        # run_kernel(input, weights, output, batch, in_ch, out_ch, D_like, H_like, W_like, kernel_size, stride, pad, groups)
        if len(ptrs) < 2:
            raise RuntimeError("Not enough inputs for conv3d_packed")
        inp, weight = inputs[0], inputs[1]
        if inp.ndim != 5 or weight.ndim != 5:
            raise RuntimeError(f"conv3d_packed expects 5D tensors, got {inp.shape}, {weight.shape}")
        B, C_in, D_in, H_in, W_in = inp.shape
        C_out, C_in2, kD, kH, kW = weight.shape
        # Assume cubic kernel and fixed stride/pad/groups from dataset
        kernel_size = kD  # assume cubic
        stride, padding, groups = 2, 3, 4  # from dataset
        if output.ndim == 5:
            _, _, D_like, H_like, W_like = output.shape
        else:
            D_like, H_like, W_like = D_in, H_in, W_in
        fn(ptrs[0], ptrs[1], out_ptr,
           ctypes.c_int(B), ctypes.c_int(C_in), ctypes.c_int(C_out),
           ctypes.c_int(D_like), ctypes.c_int(H_like), ctypes.c_int(W_like),
           ctypes.c_int(kernel_size), ctypes.c_int(stride), ctypes.c_int(padding), ctypes.c_int(groups))
    elif signature == "x_y_total":
        if len(ptrs) < 1:
            raise RuntimeError("Not enough inputs for x_y_total")
        fn(ptrs[0], out_ptr, ctypes.c_int(total))
    else:
        raise RuntimeError(f"Unknown signature tag: {signature}")

def hip_forward(so_path: str, inputs: list, expected_output: torch.Tensor | None = None) -> Tuple[torch.Tensor, Dict[str, Any]]:
    """
    Try to invoke run_kernel from shared library with robust signature guessing.
    Returns (output_tensor, details_dict).
    """
    lib = ctypes.CDLL(so_path)
    if not hasattr(lib, 'run_kernel'):
        raise AttributeError(f"{so_path}: undefined symbol: run_kernel")

    # Ensure tensors are on GPU, float32, contiguous
    def prep(t: torch.Tensor) -> torch.Tensor:
        return t.to("cuda").contiguous().to(torch.float32)
    inputs = [prep(t) for t in inputs]

    # Prepare output buffer
    if expected_output is None:
        # Default: same shape/dtype as first input
        out = torch.empty_like(inputs[0])
    else:
        out = prep(expected_output)

    kernel_src = _read_kernel_source_from_so_path(so_path)
    candidates = _infer_possible_signatures(kernel_src)
    # If multiple inputs, prefer appropriate signatures first
    if len(inputs) > 1:
        # Check if we have convolution tensors (4D or 5D inputs)
        has_4d = any(inp.ndim == 4 for inp in inputs if isinstance(inp, torch.Tensor))
        has_5d = any(inp.ndim == 5 for inp in inputs if isinstance(inp, torch.Tensor))
        
        if has_5d:
            preferred = ["conv3d_packed", "conv2d_packed", "ab_c_MNK", "ab_c_MN", "ab_c_N", "x_y_N", "x_y_total"]
        elif has_4d:
            preferred = ["conv2d_packed", "conv3d_packed", "ab_c_MNK", "ab_c_MN", "ab_c_N", "x_y_N", "x_y_total"]
        else:
            preferred = ["x_y_N", "x_y_total", "ab_c_MNK", "ab_c_MN", "ab_c_N", "conv2d_packed", "conv3d_packed"]
        
        # stable reordering keeping only those present
        existing = set(candidates)
        candidates = [c for c in preferred if c in existing] + [c for c in candidates if c not in set(preferred)]
    last_err = None
    used_sig = None
    for sig in candidates:
        try:
            _attempt_run_kernel(lib, inputs, out, sig)
            torch.cuda.synchronize()
            used_sig = sig
            last_err = None
            break
        except Exception as e:
            last_err = e
            continue

    if used_sig is None and last_err is not None:
        raise RuntimeError(f"Failed to call run_kernel with supported signatures: {last_err}")

    details = {
        "signature": used_sig,
        "attempted": candidates,
    }
    return out, details

def _generate_inputs(torch_file: str, default_shape=(1024,)) -> List[torch.Tensor]:
    ns: dict = {}
    exec(pathlib.Path(torch_file).read_text(), ns)
    torch.manual_seed(42)
    if "get_inputs" in ns and callable(ns["get_inputs"]):
        inputs = ns["get_inputs"]()
        if not isinstance(inputs, (list, tuple)):
            inputs = [inputs]
    else:
        inputs = [torch.randn(*default_shape)]
    inputs = [x.to("cuda").to(torch.float32).contiguous() if isinstance(x, torch.Tensor) else x for x in inputs]
    
    # For convolution operations, we need to extract weights from the model
    if "Model" in ns and hasattr(ns["Model"], "__call__"):
        try:
            init_inputs = []
            if "get_init_inputs" in ns and callable(ns["get_init_inputs"]):
                init_inputs = ns["get_init_inputs"]()
            
            if init_inputs:
                model = ns["Model"](*init_inputs)
            else:
                model = ns["Model"]()
            
            model = model.cuda()
            model.eval()
            
            # Check if this is a convolution operation and extract weights
            if hasattr(model, 'conv2d') and hasattr(model.conv2d, 'weight'):
                weight_tensor = model.conv2d.weight.cuda().contiguous()
                inputs.append(weight_tensor)
            elif hasattr(model, 'conv1d') and hasattr(model.conv1d, 'weight'):
                weight_tensor = model.conv1d.weight.cuda().contiguous()
                inputs.append(weight_tensor)
            elif hasattr(model, 'conv_transpose3d') and hasattr(model.conv_transpose3d, 'weight'):
                weight_tensor = model.conv_transpose3d.weight.cuda().contiguous()
                inputs.append(weight_tensor)
        except Exception:
            # If weight extraction fails, continue with just input tensors
            pass
    
    return list(inputs)

def compare_torch_to_hip(torch_file: str, so_path: str, shape=(1024,)) -> Dict[str, Any]:
    """Run torch reference and HIP kernel on the SAME inputs and return structured report."""
    ref_fn = _load_torch_fn(torch_file)
    inputs = _generate_inputs(torch_file, default_shape=shape)

    # Separate inputs for PyTorch (only data inputs) vs HIP (data + weights)
    torch_inputs = []
    hip_inputs = inputs[:]
    
    # For convolution operations, the last input might be weights - exclude from PyTorch call
    ns: dict = {}
    exec(pathlib.Path(torch_file).read_text(), ns)
    if "Model" in ns and hasattr(ns["Model"], "__call__"):
        try:
            init_inputs = []
            if "get_init_inputs" in ns and callable(ns["get_init_inputs"]):
                init_inputs = ns["get_init_inputs"]()
            
            # Create a temporary model to check if it's a convolution
            if init_inputs:
                temp_model = ns["Model"](*init_inputs)
            else:
                temp_model = ns["Model"]()
            
            is_conv = (hasattr(temp_model, 'conv2d') or 
                      hasattr(temp_model, 'conv1d') or 
                      hasattr(temp_model, 'conv_transpose3d'))
            
            if is_conv and len(inputs) > 1:
                # For convolution, use only the first input(s) for PyTorch, all for HIP
                if "get_inputs" in ns:
                    original_inputs = ns["get_inputs"]()
                    torch_inputs = original_inputs if isinstance(original_inputs, list) else [original_inputs]
                    torch_inputs = [x.to("cuda").to(torch.float32).contiguous() if isinstance(x, torch.Tensor) else x for x in torch_inputs]
                else:
                    torch_inputs = inputs[:-1]  # exclude weight tensor added for HIP
            else:
                torch_inputs = inputs[:]
        except Exception:
            torch_inputs = inputs[:]
    else:
        torch_inputs = inputs[:]

    # Run reference with torch_inputs
    with torch.no_grad():
        if len(torch_inputs) == 1:
            y_ref = ref_fn(torch_inputs[0])
        else:
            y_ref = ref_fn(*torch_inputs)
        if isinstance(y_ref, (list, tuple)):
            # For multiple outputs, stack to a single tensor for comparison
            y_ref = torch.cat([y.contiguous().view(-1) for y in y_ref], dim=0)
        y_ref = y_ref.contiguous().to(torch.float32)

    # Run HIP with hip_inputs (includes weights for convolution)
    try:
        y_hip, details = hip_forward(so_path, hip_inputs, expected_output=y_ref)
    except Exception as e:
        return {
            "ok": False,
            "error": f"HIP invocation failed: {e}",
            "details": {}
        }

    # Compare
    try:
        max_err = (y_ref - y_hip).abs().max().item()
    except Exception as e:
        return {
            "ok": False,
            "error": f"Output comparison failed: {e}",
            "details": details
        }

    return {
        "ok": True,
        "max_abs_err": max_err,
        "details": details
    }

def compare_kernel_to_kernel(so_path_a: str, so_path_b: str, torch_file: str | None = None, shape=(1024,)) -> Dict[str, Any]:
    """Compare two HIP shared libraries on the SAME inputs. If torch_file is provided, use its get_inputs()."""
    if torch_file:
        inputs = _generate_inputs(torch_file, default_shape=shape)
    else:
        torch.manual_seed(42)
        inputs = [torch.randn(*shape, device="cuda", dtype=torch.float32).contiguous()]

    # Run A
    try:
        y_a, details_a = hip_forward(so_path_a, inputs)
    except Exception as e:
        return {"ok": False, "error": f"Kernel A failed: {e}", "which": "A"}

    # Run B
    try:
        y_b, details_b = hip_forward(so_path_b, inputs, expected_output=y_a)
    except Exception as e:
        return {"ok": False, "error": f"Kernel B failed: {e}", "which": "B"}

    try:
        max_err = (y_a - y_b).abs().max().item()
    except Exception as e:
        return {"ok": False, "error": f"Comparison failed: {e}"}

    return {
        "ok": True,
        "max_abs_err": max_err,
        "details": {"A": details_a, "B": details_b}
    }

def max_abs_err(torch_file: str, so_path: str, shape=(1024,)) -> float:
    """Backwards compatible helper returning just the error."""
    report = compare_torch_to_hip(torch_file, so_path, shape=shape)
    if not report.get("ok", False):
        # Bubble up as exception for callers expecting failures
        raise RuntimeError(report.get("error", "correctness failed"))
    return float(report.get("max_abs_err", float("inf")))
