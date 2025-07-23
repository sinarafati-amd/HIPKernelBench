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

def compile_hip(source_code: str) -> str:
    path = tempfile.mkdtemp(prefix="hip_")
    hip_file = os.path.join(path, "kernel.hip")
    print('gpu code at: '+hip_file)
    print('*'*120)
    
    with open(hip_file, "w") as f:
        f.write(source_code)
    out_name = os.path.join(path, "kernel.out")
    so_name = os.path.join(path, "kernel.so")
    
    # Compile executable (for profiling)
    cmd = ["hipcc", hip_file, "-o", out_name]
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
        raise RuntimeError(e.output)
    
    # Compile shared library (for correctness checking)
    cmd_so = ["hipcc", "-shared", "-fPIC", hip_file, "-o", so_name]
    try:
        output_so = subprocess.run(cmd_so, capture_output=True, text=True, env=os.environ)
        stdout_so = output_so.stdout
        stderr_so = output_so.stderr

        if output_so.returncode != 0:
            log.append({"event": "compile_warning_so", "stdout": stdout_so, "stderr": stderr_so})
            # Don't fail if shared library compilation fails, just log it

    except subprocess.CalledProcessError as e:
        log.append({"event": "compile_warning_so", "stdout": e.output, "stderr": e.stderr})
        # Don't fail if shared library compilation fails

    return out_name, stdout, stderr, hip_file

