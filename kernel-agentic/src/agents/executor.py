import shutil
import subprocess, yaml, tempfile, os, textwrap
import os 
import sys 
from pathlib import Path
dir_path=str(Path(os.path.dirname(Path(__file__))).parent)
if dir_path not in sys.path:
    sys.path.append(dir_path)

from utils.kernel_compiler import compile_kernel
from utils.rocprof_parser import collect_metrics, profile
from utils.logger import log
from typing import Optional, Tuple, Dict


CFG = yaml.safe_load(open("config.yml"))


class Executor:
    def __init__(self, kernel_lang: str = None):
        import yaml
        CFG = yaml.safe_load(open("config.yml"))
        if kernel_lang is None:
            kernel_lang = CFG.get("Pipeline", {}).get("kernel_lang", "hip")
        self.kernel_lang = kernel_lang.lower()
    
    def run(self, kernel_code: str) -> Tuple[Optional[Dict], Optional[str]]:
        if self.kernel_lang == "hip":
            return self.run_hip(kernel_code)
        else:
            raise ValueError(f"Unsupported kernel language: {self.kernel_lang}")

        bin_path, stdout, stderr, kernel_file = compile_kernel(kernel_code, self.kernel_lang)
        # if there was a compilation/runtime error, return no stats and the stderr
        if stderr:
            log.append({
                "event": "execution_error",
                "stderr": stderr.strip()
            })
            return None, stderr, kernel_file
        # otherwise profile the binary
        bin_path = bin_path.replace('.so','.hip')

        # TODO: We passed kernel language to the above compile_kernel function
        #       but we assume it's hip code in the profile function
        stats = profile(bin_path)
        log.append({
            "event": "execution_stats",
            **stats
        })
        return stats, None, kernel_file


    def run_hip(self, kernel_code: str) -> Tuple[Optional[Dict], Optional[str]]:
        if shutil.which("hipcc") is None:
            raise RuntimeError("hipcc not found. Please install ROCm HIP compiler.")
        
        if shutil.which("rocprof-compute") is None:
            raise RuntimeError("rocprof-compute not found. Please install ROCm profiling tools.")
        
        temp_dir = tempfile.mkdtemp(prefix="hip_")
        hip_file = os.path.join(temp_dir, "kernel.hip")
        out_name = os.path.join(temp_dir, "kernel.out")
        so_name = os.path.join(temp_dir, "kernel.so")


        with open(hip_file, 'w') as f:
            f.write(kernel_code)
        
        print('gpu code at: ' + hip_file)
        print('*'*120)

        compile_cmd = [
            "hipcc", "-O3", "-std=c++17", f"--offload-arch={CFG['hip']['gpu_arch']}", 
            "-g", hip_file, "-o", out_name
        ]
        so_cmd = ["hipcc", "-shared", "-fPIC", hip_file, "-o", so_name]

        try:
            result_bin = subprocess.run(compile_cmd, capture_output=True, text=True, env=os.environ)
            if result_bin.returncode != 0:
                log.append({"event": "compile_error", "stdout": result_bin.stdout, "stderr": result_bin.stderr})
                raise subprocess.CalledProcessError(result_bin.returncode, compile_cmd, output=result_bin.stdout, stderr=result_bin.stderr)


            result_so = subprocess.run(so_cmd, capture_output=True, text=True, env=os.environ)
            if result_so.returncode != 0:
                log.append({"event": "compile_error", "stdout": result_so.stdout, "stderr": result_so.stderr})
            
            print("Compilation successful!")
        
        except Exception as e:
            print(f"Compilation failed. Error: {e}")
            log.append({"event": "compile_error", "stdout": e.output, "stderr": e.stderr})
            raise RuntimeError(e.output)
        
        
        # Run profiling
        print("Running profiling...")
        profile_output_path = os.path.join(temp_dir, "profile_output")
        os.makedirs(profile_output_path, exist_ok=True)
        profile_cmd = [
            "rocprof-compute", "profile", "-n", "kernelgen", "--path", "profile_output", "--no-roof", \
                "--join-type", "kernel", "--", out_name
        ]
        try:
            env = os.environ.copy()
            env['LC_ALL'] = 'C'
            env['LANG'] = 'C'
            
            result = subprocess.run(profile_cmd, capture_output=True, text=True, env=os.environ, cwd=temp_dir)

            if result.returncode != 0:
                print(f"Profiling failed with return code {result.returncode}")
                print(f"stdout: {result.stdout}")
                print(f"stderr: {result.stderr}")
                log.append({"event": "profiling_error", "stdout": result.stdout, "stderr": result.stderr})
                raise RuntimeError(f"Profiling failed: {result.stderr}")

        except Exception as e:
            print(f"Profiling failed. Error: {e}")
            log.append({"event": "profiling_error", "stdout": e.output, "stderr": e.stderr})
            raise RuntimeError(e.output)
        
        
        # Check if profiling files were created
        temp_dir = Path(temp_dir)
        profile_ouput_dir = temp_dir / "profile_output"
        if not profile_ouput_dir.exists():
            print(f"Profiling output directory not found: {profile_ouput_dir}")
            raise RuntimeError("Profiling output directory not found")
        
        # Collect metrics using existing function
        metrics_dict = collect_metrics(profile_ouput_dir)

        log.append({"event": "execution_stats", **metrics_dict})
        print("Profiling successful!")

        return metrics_dict, None, hip_file