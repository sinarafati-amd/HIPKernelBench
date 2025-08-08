import json, os, re, subprocess, functools

@functools.lru_cache
def get_gpu_specs() -> dict:
    """
    Try parsing AMD GPU specs via `rocminfo`. If `--json` fails, use plain text fallback.
    Extract specs from the first available GPU agent.
    """
    try:
        # First try the JSON format (though it might not be available)
        out = subprocess.check_output(["rocminfo", "--json"], text=True)
        # If we get here, JSON format worked
        data = json.loads(out)
        # Find the first GPU agent
        for agent in data.get("agents", []):
            if agent.get("device_type") == "GPU":
                return {
                    "arch": agent.get("name", "unknown"),
                    "cu_count": agent.get("compute_unit", 0),
                    "wavefront": agent.get("wavefront_size", 64),
                    "shared_mem": agent.get("shared_memory_size", 64 * 1024),  # Default 64KB
                    "max_sclk_mhz": agent.get("max_clock_freq_mhz", 1700)
                }
        raise ValueError("No GPU agent found in JSON output")
    except Exception:
        try:
            out = subprocess.check_output(["rocminfo"], text=True)
            # Find the block for the first GPU agent (e.g. gfx90a)
            blocks = out.split("*******")
            gpu_block = None
            for block in blocks:
                if "Device Type:             GPU" in block:
                    gpu_block = block
                    break
            
            if not gpu_block:
                raise ValueError("No GPU agent found in rocminfo output")

            def grab(label, pattern, cast=int):
                m = re.search(pattern, gpu_block)
                return cast(m.group(1)) if m else None

            # Extract GPU specs from the block
            arch = grab("arch", r"Name:\s+([^\s]+)", str)
            cu_count = grab("cu", r"Compute Unit:\s+(\d+)")
            wavefront = grab("wavefront", r"Wavefront Size:\s+(\d+)")
            max_sclk_mhz = grab("clk", r"Max Clock Freq. \(MHz\):\s+(\d+)")
            
            # For shared memory, we need to look for L1 cache size or use a default
            # L1 cache is typically 16KB per CU for AMD GPUs
            shared_mem = grab("l1", r"L1:\s+(\d+)\([^)]*\)\s+KB", lambda x: int(x)*1024)
            if shared_mem is None:
                # Default to 64KB per CU if we can't find L1 cache info
                shared_mem = (cu_count or 104) * 64 * 1024  # 64KB per CU
            else:
                # For AMD GPUs, LDS (Local Data Share) is 64KB per CU, not the L1 cache size
                # L1 cache is separate from shared memory
                shared_mem = (cu_count or 104) * 64 * 1024  # 64KB per CU for LDS

            return {
                "arch": arch or "gfx90a",
                "cu_count": cu_count or 104,
                "wavefront": wavefront or 64,
                "shared_mem": shared_mem,
                "max_sclk_mhz": max_sclk_mhz or 1700
            }

        except Exception as e:
            # If all else fails, return default specs for MI250X
            print(f"Warning: Could not parse GPU info from `rocminfo`: {e}")
            print("Using default specs for AMD Instinct MI250X")
            return {
                "arch": "gfx90a",
                "cu_count": 104,
                "wavefront": 64,
                "shared_mem": 104 * 64 * 1024,  # 64KB per CU
                "max_sclk_mhz": 1700
            }
