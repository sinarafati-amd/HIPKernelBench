import csv
import json
import pathlib
import subprocess
import tempfile
import os
import time

def _extract_kernel_time_from_csv(csv_file):
    """
    The profiling tool outputs csv files which need to be parsed
    """
    with open(csv_file, 'r') as f:
        total_duration_ns = sum(float(row['TotalDurationNs']) for row in csv.DictReader(f))
        # Convert nanoseconds to microseconds
        return total_duration_ns / 1000.0

def baseline_latency(
    torch_file: str,
    n_trial: int = 30,
    warmup: int = 1,
    regenerate_inputs: bool = False,
) -> float:
    """
    Measure the reference latency of code in `torch_file` on ROCm GPU
    using the rocprof tool.

    Returns average latency in **micro-seconds**.
    """
    # Extract the filename stem for reporting
    stem = pathlib.Path(torch_file).stem

    # Create a simple wrapper script that imports and runs the model
    # Fix: Use double braces to escape curly braces in f-string
    wrapper_script = f"""
import torch
import pathlib
import time

# Load the model file
ns = {{}}
exec(compile(pathlib.Path("{torch_file}").read_text(), "{torch_file}", "exec"), ns)

# Prepare model and inputs
if 'get_inputs' in ns and 'Model' in ns:
    model = ns['Model'](*ns.get('get_init_inputs', lambda: [])()).eval().cuda()
    torch.backends.cudnn.benchmark = True

    # Run warmup iterations
    for _ in range({warmup}):
        inputs = [x.cuda() for x in ns['get_inputs']()]
        _ = model(*inputs)

    # Force CUDA synchronization before the measured region
    torch.cuda.synchronize()

    # Run the actual operation we want to measure
    inputs = [x.cuda() for x in ns['get_inputs']()]
    output = model(*inputs)

    # Force synchronization to ensure all GPU work is complete
    torch.cuda.synchronize()

    # --- Cleanup ---
    # 1. Delete Python references to tensors and models
    del inputs
    del output
    del model

    # 2. Empty CUDA cache to release memory back to the GPU
    torch.cuda.empty_cache()

    # 3. Reset the CUDA device (more aggressive cleanup)
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
        # Optional: even more aggressive reset (use cautiously)
        # torch.cuda.reset_max_memory_allocated()
        # torch._C._cuda_resetAccumulatedMemoryStats()
    
    # 4. Wait for any remaining operations
    torch.cuda.synchronize()

    print("GPU cleanup completed successfully")
else:
    print("Error: Model file must define get_inputs() and Model class")
    exit(1)
"""

    # Write the wrapper script to a temporary file
    with tempfile.NamedTemporaryFile(suffix='.py', delete=False, mode='w') as f:
        f.write(wrapper_script)
        wrapper_path = f.name

    with tempfile.TemporaryDirectory() as temp_dir:
        # Path for rocprof output JSON
        res_basename = "rocprof_output"
        output_csv = pathlib.Path(temp_dir) / f'{res_basename}.csv'

        # rocprof command to collect kernel execution times
        rocprof_cmd = [
            "rocprof",
            "--stats",
            "--basenames",
            "on",
            "-o", output_csv,
            "python", wrapper_path
        ]

        all_latencies = []

        for trial in range(n_trial):
            print(f"Running trial {trial+1}/{n_trial} for PyTorch {stem}...", end=" ", flush=True)
            subprocess.run(rocprof_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            # TODO there is also a `f'{res_basename}.csv'` file (without the `.stats` in the name) which has
            # beginning and ending timestamps of kernel runs (instead of `TotalDurationNs` which we're parsing).
            # It's worth validating that the elapsed time between these begin-and-end timestamps correspond to
            # the numbers we're parsing in the `.stats.csv` as a sanity check.
            total_time_us = _extract_kernel_time_from_csv(pathlib.Path(temp_dir) / f'{res_basename}.stats.csv')
            all_latencies.append(total_time_us)
            print(f"completed ({total_time_us:.2f} µs)")

    os.unlink(wrapper_path)
    
    lat_us = sum(all_latencies) / len(all_latencies)
    print(f"Average latency for {stem}: {lat_us:.2f} µs")
    return lat_us
