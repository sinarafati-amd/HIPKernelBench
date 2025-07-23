import torch, ctypes, pathlib, inspect, runpy
from types import ModuleType
from typing import Callable

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
            
            model.eval()  # Set to evaluation mode
            with torch.no_grad():
                return model(*args)
        
        return model_forward
    
    raise RuntimeError("Need either run_ref(), a single *_ref function, or a Model class")

def hip_forward(so_path: str, inputs: list) -> torch.Tensor:
    lib = ctypes.CDLL(so_path)
    
    # Check if run_kernel symbol exists
    if not hasattr(lib, 'run_kernel'):
        raise AttributeError(f"{so_path}: undefined symbol: run_kernel")
    
    lib.run_kernel.argtypes  = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
    lib.run_kernel.restype   = None
    
    if len(inputs) == 1:
        # Single input case
        x = inputs[0]
        y = torch.empty_like(x)
        size = x.numel()
    else:
        # Multiple inputs case - pack them into a single buffer
        total_elements = sum(inp.numel() for inp in inputs)
        x = torch.cat([inp.flatten() for inp in inputs])
        # Assume output has same shape as first input (most common case)
        y = torch.empty_like(inputs[0])
        size = inputs[0].numel()  # Size parameter is typically for the output size
    
    lib.run_kernel(
        ctypes.c_void_p(x.data_ptr()),
        ctypes.c_void_p(y.data_ptr()),
        ctypes.c_int(size)
    )
    torch.cuda.synchronize()
    return y

def max_abs_err(torch_file: str, so_path: str, shape=(1024,)) -> float:
    # Load the torch file and get both reference function and inputs
    ns: dict = {}
    exec(pathlib.Path(torch_file).read_text(), ns)
    
    # Get reference function using the helper
    ref_fn = _load_torch_fn(torch_file)
    
    # Get inputs from get_inputs() function if available, otherwise use default shape
    if "get_inputs" in ns and callable(ns["get_inputs"]):
        inputs = ns["get_inputs"]()
        if inputs and len(inputs) > 0:
            # Convert inputs to GPU and float32 if needed
            inputs = [inp.to("cuda").float() if isinstance(inp, torch.Tensor) else inp for inp in inputs]
        else:
            inputs = [torch.randn(*shape, device="cuda", dtype=torch.float32)]
    else:
        inputs = [torch.randn(*shape, device="cuda", dtype=torch.float32)]
    
    # Run reference function with inputs
    if len(inputs) == 1:
        y_ref = ref_fn(inputs[0])
        y_hip = hip_forward(so_path, inputs)
    else:
        y_ref = ref_fn(*inputs)
        y_hip = hip_forward(so_path, inputs)
    
    return (y_ref - y_hip).abs().max().item()
