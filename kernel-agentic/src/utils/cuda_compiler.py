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

def compile_cuda(source_code: str) -> str:
    path = tempfile.mkdtemp(prefix="cuda_")
    cuda_file = os.path.join(path, "kernel.cu")
    print('gpu code at: '+cuda_file)
    print('*'*120)
    
    with open(cuda_file, "w") as f:
        f.write(source_code)
    out_name = os.path.join(path, "kernel.out")
    cmd = ["nvcc", cuda_file, "-o", out_name]
    try:
        output = subprocess.run(cmd, capture_output=True, text=True, env=os.environ)
        stdout = output.stdout
        stderr = output.stderr

        if output.returncode != 0:
            log.append({"event": "compile_error", "stdout": stdout, "stderr": stderr})
            raise subprocess.CalledProcessError(output.returncode, cmd, output=stdout, stderr=stderr)

    except subprocess.CalledProcessError as e:
        stdout = e.output
        stderr = e.stderr
        log.append({"event": "compile_error", "stdout": stdout, "stderr": stderr})
        raise RuntimeError(digest(e.output))

    return out_name, stdout, stderr, cuda_file
