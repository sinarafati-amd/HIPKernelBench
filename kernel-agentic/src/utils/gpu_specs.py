import json, os, re, subprocess, functools

@functools.lru_cache
def get_gpu_specs() -> dict:
    """
    Try parsing AMD GPU specs via `rocminfo`. If `--json` fails, use plain text fallback.
    Extract specs from the first available GPU agent.
    """
    try:
        out = subprocess.check_output(["rocminfo", "--json"], text=True)
        raise ValueError("system's `rocminfo` does not return JSON. Fallback activated.")
    except Exception:
        try:
            out = subprocess.check_output(["rocminfo"], text=True)
            # Find the block for the first GPU agent (e.g. gfx942)
            blocks = out.split("*******")
            gpu_block = next(b for b in blocks if "Device Type:             GPU" in b)

            def grab(label, pattern, cast=int):
                m = re.search(pattern, gpu_block)
                return cast(m.group(1)) if m else None

            return {
                "arch"         : grab("arch", r"Name:\s+([^\s]+)", str),
                "cu_count"     : grab("cu", r"Compute Unit:\s+(\d+)"),
                "wavefront"    : grab("wavefront", r"Wavefront Size:\s+(\d+)"),
                "shared_mem"   : grab("smem", r"Size:\s+(\d+)\([^)]*\)\s+KB", lambda x: int(x)*1024),
                "max_sclk_mhz" : grab("clk", r"Max Clock Freq. \(MHz\):\s+(\d+)")
            }

        except Exception as e:
            raise RuntimeError(f"Could not parse GPU info from `rocminfo`: {e}")
