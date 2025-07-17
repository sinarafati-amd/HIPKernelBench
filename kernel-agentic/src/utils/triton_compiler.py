import subprocess, tempfile, os, uuid, textwrap, yaml, json
import os 
import sys 
from pathlib import Path
dir_path=str(Path(os.path.dirname(Path(__file__))).parent)
if dir_path not in sys.path:
    sys.path.append(dir_path)
from utils.logger import log
from utils.error_digester import digest

CFG = yaml.safe_load(open("config.yml"))

def compile_triton(source_code: str) -> str:
    """
    Triton compilation is different - it's Python-based and JIT compiled.
    This is a simplified version that would need proper Triton integration.
    """
    path = tempfile.mkdtemp(prefix="triton_")
    triton_file = os.path.join(path, "kernel.py")
    print('gpu code at: '+triton_file)
    print('*'*120)
    
    with open(triton_file, "w") as f:
        f.write(source_code)
    
    # For Triton, we would typically import and JIT compile the kernel
    # This is a placeholder - real implementation would use triton.jit
    out_name = os.path.join(path, "kernel.py")  # Triton kernels are Python files
    
    try:
        # Basic syntax check for Python code
        with open(triton_file, 'r') as f:
            code = f.read()
        compile(code, triton_file, 'exec')
        
        stdout = "Triton kernel syntax check passed"
        stderr = ""
        
    except SyntaxError as e:
        stderr = f"Triton syntax error: {str(e)}"
        stdout = ""
        log.append({"event": "compile_error", "stdout": stdout, "stderr": stderr})
        raise RuntimeError(digest(stderr))
    except Exception as e:
        stderr = f"Triton compilation error: {str(e)}"
        stdout = ""
        log.append({"event": "compile_error", "stdout": stdout, "stderr": stderr})
        raise RuntimeError(digest(stderr))

    return out_name, stdout, stderr, triton_file
