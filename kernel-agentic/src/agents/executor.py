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
        
        # Check which profiler to use based on config
        profiler = CFG.get("gpu_specs", {}).get("profiler", "rocprof-compute")
        
        if profiler == "rocprof-compute":
            if shutil.which("rocprof-compute") is None:
                raise RuntimeError("rocprof-compute not found. Please install ROCm profiling tools.")
        elif profiler == "rocprof":
            if shutil.which("rocprof") is None:
                raise RuntimeError("rocprof not found. Please install ROCm profiling tools.")
        else:
            raise ValueError(f"Unsupported profiler: {profiler}. Use 'rocprof' or 'rocprof-compute'")
        
        temp_dir = tempfile.mkdtemp(prefix="hip_")
        hip_file = os.path.join(temp_dir, "kernel.hip")
        out_name = os.path.join(temp_dir, "kernel.out")
        so_name = os.path.join(temp_dir, "kernel.so")


        with open(hip_file, 'w') as f:
            f.write(kernel_code)
        
        print('gpu code at: ' + hip_file)
        print('*'*120)

        compile_cmd = [
            "hipcc", "-O3", "-std=c++17", f"--offload-arch={CFG['gpu_specs']['arch']}", 
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
        
        
        # Before profiling: basic sandbox check to avoid GPU faults
        print("Running sandbox preflight...")
        try:
            # Run in a separate process to prevent whole-run crash
            import json
            from pathlib import Path as _Path
            script_path = _Path(__file__).resolve().parent.parent / "utils" / "sandbox_check.py"
            # Ensure project root is on PYTHONPATH for 'src' imports
            env = os.environ.copy()
            from pathlib import Path as _Path
            project_root = str(_Path(__file__).resolve().parents[2])
            env["PYTHONPATH"] = project_root + (":" + env["PYTHONPATH"] if "PYTHONPATH" in env else "")

            preflight = subprocess.run([
                sys.executable, str(script_path), "--so", so_name
            ], capture_output=True, text=True, env=env, cwd=os.path.dirname(hip_file))
            ok = False
            details = {}
            if preflight.stdout:
                try:
                    details = json.loads(preflight.stdout.strip().splitlines()[-1])
                    ok = bool(details.get("ok", False))
                except Exception:
                    ok = False
            if not ok:
                msg = f"Sandbox preflight failed: {details.get('error', preflight.stderr.strip())}"
                print(msg)
                log.append({"event": "sandbox_failed", "details": details, "stderr": preflight.stderr})
                # Degrade gracefully: return minimal stats so upstream can log errors/correctness
                return {"avg_us": None}, msg, hip_file
        except Exception as e:
            # If sandbox itself errors, continue but note it
            print(f"Sandbox preflight error: {e}")
            log.append({"event": "sandbox_error", "error": str(e)})

        # Run profiling
        print("Running profiling...")
        profile_output_path = os.path.join(temp_dir, "profile_output")
        os.makedirs(profile_output_path, exist_ok=True)
        
        # Use appropriate profiler based on config
        profiler = CFG.get("gpu_specs", {}).get("profiler", "rocprof-compute")
        
        if profiler == "rocprof-compute":
            profile_cmd = [
                "rocprof-compute", "profile", "-n", "kernelgen", "--path", "profile_output", "--no-roof", \
                    "--join-type", "kernel", "--", out_name
            ]
        elif profiler == "rocprof":
            # For rocprof, we need to use the profile function from rocprof_parser
            # which expects the binary path directly
            from utils.rocprof_parser import profile
            try:
                metrics_dict = profile(out_name)
                log.append({"event": "execution_stats", **metrics_dict})
                print("Profiling successful!")
                return metrics_dict, None, hip_file
            except Exception as e:
                print(f"Profiling failed. Error: {e}")
                log.append({"event": "profiling_error", "error": str(e), "profiler": "rocprof"})
                # Attempt fallback to rocprof-compute if available
                if shutil.which("rocprof-compute") is not None:
                    profiler = "rocprof-compute"
                    profile_cmd = [
                        "rocprof-compute", "profile", "-n", "kernelgen", "--path", "profile_output", "--no-roof", \
                            "--join-type", "kernel", "--", out_name
                    ]
                else:
                    # Graceful degradation: skip profiling but return success so correctness can proceed
                    print("Profiling unavailable; skipping and continuing without metrics.")
                    metrics_dict = {"avg_us": None}
                    log.append({"event": "profiling_skipped", "reason": "rocprof failed and no rocprof-compute"})
                    return metrics_dict, None, hip_file
        else:
            raise ValueError(f"Unsupported profiler: {profiler}")
        
        # Only execute rocprof-compute if we're using that profiler
        if profiler == "rocprof-compute":
            try:
                env = os.environ.copy()
                env['LC_ALL'] = 'C'
                env['LANG'] = 'C'
                
                result = subprocess.run(profile_cmd, capture_output=True, text=True, env=os.environ, cwd=temp_dir)

                # Check return code and stderr for failures
                if result.returncode != 0:
                    raise RuntimeError(result.stderr or f"rocprof-compute exited {result.returncode}")

            except Exception as e:
                print(f"Profiling failed. Error: {e}")
                log.append({"event": "profiling_error", "error": str(e), "profiler": "rocprof-compute"})
                # Graceful degradation: skip profiling but return success so correctness can proceed
                metrics_dict = {"avg_us": None}
                log.append({"event": "profiling_skipped", "reason": "rocprof-compute failed"})
                return metrics_dict, None, hip_file
            
            
            # Check if profiling files were created
            temp_dir = Path(temp_dir)
            profile_ouput_dir = temp_dir / "profile_output"
            if not profile_ouput_dir.exists():
                print(f"Profiling output directory not found: {profile_ouput_dir}")
                raise RuntimeError("Profiling output directory not found")
            
            # Collect metrics using existing function for rocprof-compute
            metrics_dict = collect_metrics(profile_ouput_dir)

            log.append({"event": "execution_stats", **metrics_dict})
            print("Profiling successful!")

            return metrics_dict, None, hip_file