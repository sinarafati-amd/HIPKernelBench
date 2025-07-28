import json
import pathlib
import subprocess
import tempfile
import os
import re
import time
from typing import Dict, Any

def baseline_latency(
    torch_file: str,
    n_trial: int = 30,
    warmup: int = 1,
    regenerate_inputs: bool = False,
) -> float:
    """
    Measure (and cache) the reference latency of code in `torch_file` on ROCm GPU
    using the rocprof tool.

    Returns average latency in **micro-seconds**.
    """
    # Extract the filename stem for caching and reporting
    stem = pathlib.Path(torch_file).stem
    cache_path = pathlib.Path("logs") / stem / f"baseline_{stem}.json"

    # Check if we have cached results
    if cache_path.exists():
        return json.load(open(cache_path))["lat_us"]

    # Create a simple wrapper script that imports and runs the model
    wrapper_script = f"""
import torch
import pathlib
import time

# Load the model file
ns = {"{"}
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
    _ = model(*inputs)
    
    # Force synchronization to ensure all GPU work is complete
    torch.cuda.synchronize()
else:
    print("Error: Model file must define get_inputs() and Model class")
    exit(1)
"""

    # Write the wrapper script to a temporary file
    with tempfile.NamedTemporaryFile(suffix='.py', delete=False, mode='w') as f:
        f.write(wrapper_script)
        wrapper_path = f.name

    # Create a temporary directory for rocprof output
    with tempfile.TemporaryDirectory() as temp_dir:
        # Path for rocprof output JSON
        output_json = os.path.join(temp_dir, "rocprof_output.json")
        
        # rocprof command to collect kernel execution times
        rocprof_cmd = [
            "rocprof",
            "--hip-trace",
            "--timestamp",
            "-o", output_json,
            "python", wrapper_path
        ]
        
        all_latencies = []
        
        # Run multiple trials
        for trial in range(n_trial):
            print(f"Running trial {trial+1}/{n_trial} for {stem}...", end=" ", flush=True)
            
            try:
                # Run the profiling
                subprocess.run(rocprof_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                
                # Parse the rocprof output to get the kernel execution time
                with open(output_json, 'r') as f:
                    profile_data = json.load(f)
                
                # Calculate total kernel execution time (in microseconds)
                # Rocprof records timestamps in nanoseconds
                kernel_times = []
                for entry in profile_data:
                    if 'BeginNs' in entry and 'EndNs' in entry and 'Name' in entry:
                        duration_ns = entry['EndNs'] - entry['BeginNs']
                        kernel_times.append(duration_ns / 1000.0)  # Convert to microseconds
                
                # Get the total execution time
                if kernel_times:
                    total_time_us = sum(kernel_times)
                    all_latencies.append(total_time_us)
                    print(f"completed ({total_time_us:.2f} µs)")
                else:
                    print("No kernel executions found in profiler output")
            
            except subprocess.CalledProcessError as e:
                print(f"Error running rocprof: {e}")
                # If profiling fails, try a simple time-based measurement as fallback
                try:
                    start = time.time()
                    subprocess.run(["python", wrapper_path], check=True)
                    end = time.time()
                    latency_us = (end - start) * 1e6  # seconds to microseconds
                    all_latencies.append(latency_us)
                    print(f"completed with fallback timing ({latency_us:.2f} µs)")
                except Exception as inner_e:
                    print(f"Fallback timing also failed: {inner_e}")
        
        # Calculate the average latency
        if all_latencies:
            lat_us = sum(all_latencies) / len(all_latencies)
            print(f"Average latency for {stem}: {lat_us:.2f} µs")
        else:
            print(f"No valid measurements for {stem}, using default value")
            lat_us = 0.0
    
    # Clean up the temporary wrapper script
    try:
        os.unlink(wrapper_path)
    except:
        pass
    
    # Cache the results
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    result_data = {
        "lat_us": lat_us,
        "measurements": len(all_latencies),
        "trials": n_trial
    }
    json.dump(result_data, open(cache_path, "w"))
    
    return lat_us
