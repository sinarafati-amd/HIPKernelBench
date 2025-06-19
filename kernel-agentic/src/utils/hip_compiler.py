import subprocess, tempfile, os, uuid, textwrap, yaml, json
from .logger import log

CFG = yaml.safe_load(open("config.yml"))

def compile_hip(source_code: str) -> str:
    path = tempfile.mkdtemp(prefix="hip_")
    hip_file = os.path.join(path, "kernel.hip")
    print('gpu code at: '+hip_file)
    print('*'*120)
    
    with open(hip_file, "w") as f:
        f.write(source_code)
    out_name = os.path.join(path, "kernel.out")
    cmd = ["hipcc", hip_file, "-o", out_name]
    cmd2 = ["hipcc","-fPIC", "-shared", hip_file, "-o",  out_name := os.path.join(path, "kernel.so")]
    try:
        output = subprocess.run(cmd, capture_output=True, text=True, env=os.environ)
        output2 = subprocess.run(cmd2, capture_output=True, text=True, env=os.environ)
        stdout = output.stdout
        stderr = output.stderr

        if output.returncode != 0:
            log.append({"event": "compile_error", "stdout": stdout, "stderr": stderr})
            raise subprocess.CalledProcessError(output.returncode, cmd, output=stdout, stderr=stderr)

    except subprocess.CalledProcessError as e:
        stdout = e.output
        stderr = e.stderr
        log.append({"event": "compile_error", "stdout": stdout, "stderr": stderr})
        raise

    return out_name, stdout, stderr, hip_file

