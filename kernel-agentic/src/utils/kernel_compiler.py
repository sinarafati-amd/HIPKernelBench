import yaml
from .hip_compiler import compile_hip
from .cuda_compiler import compile_cuda
from .triton_compiler import compile_triton

CFG = yaml.safe_load(open("config.yml"))

def compile_kernel(source_code: str, kernel_lang: str = None) -> tuple:
    """
    Compile kernel code based on the specified language.
    
    Args:
        source_code: The kernel source code
        kernel_lang: The kernel language ('hip', 'cuda', 'triton'). 
                    If None, uses config.yml Pipeline.kernel_lang
    
    Returns:
        tuple: (out_name, stdout, stderr, source_file)
    """
    if kernel_lang is None:
        kernel_lang = CFG.get("Pipeline", {}).get("kernel_lang", "hip").lower()
    
    kernel_lang = kernel_lang.lower()
    
    if kernel_lang == "hip":
        return compile_hip(source_code)
    elif kernel_lang == "cuda":
        return compile_cuda(source_code)
    elif kernel_lang == "triton":
        return compile_triton(source_code)
    else:
        raise ValueError(f"Unsupported kernel language: {kernel_lang}. Supported: hip, cuda, triton")
