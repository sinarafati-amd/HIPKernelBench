from __future__ import annotations
import csv, json, subprocess, tempfile, os, statistics, collections, re
from pathlib import Path
from .logger import log
# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def _run_rocprof(bin_path: str, workdir: str) -> dict[str, Path]:
    """
    Launch rocprof in combined mode (stats + hip trace + sysinfo).
    Returns a dict with file paths created by rocprof.
    """
    prefix = Path(workdir) / "results"
    prefix.mkdir(parents=True, exist_ok=True)
    # rocprof will append suffixes:  .csv  .stats.csv  .json  .sysinfo.txt
    cmd = [
        "rocprof",
        "--stats",
        "--hip-trace",
        "--sys-trace",
        "--basenames", "on",
        "-o", str(prefix / "results.csv"),
        bin_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        log.append({"event": "profile_error",
                    "stderr": e.stderr, "stdout": e.stdout})
        raise RuntimeError(f"rocprof failed (exit {e.returncode})")
    paths = {
        "stats_csv": prefix / "results.stats.csv",
        "json":      prefix / "results.json",
        "sysinfo":   prefix / "results.sysinfo.txt",
    }
    for p in paths.values():
        if not p.exists():
            raise FileNotFoundError(f"rocprof did not create {p}")
    return paths
def _parse_stats_csv(csv_path: Path) -> list[float]:
    """Return per-kernel durations (μs) from *.stats.csv."""
    durs = []
    with csv_path.open() as fp:
        for row in csv.DictReader(fp):
            try:
                durs.append(float(row["AverageNs"]) / 1e3)
            except (KeyError, ValueError):
                pass
    return durs
def _parse_trace_json(json_path: Path) -> dict[str, float]:
    """
    Extract avg VGPR/SGPR/LDS, block_size, grid_size and IO time from traceEvents.
    """
    if not json_path.exists():
        return {}
    with json_path.open() as f:
        events = json.load(f)["traceEvents"]
    acc = collections.defaultdict(list)
    io_durs = 0.0
    total_durs = 0.0
    for ev in events:
        args = ev.get("args", {})
        if "DurationNs" not in args:
            continue
        dur_us = int(args["DurationNs"]) / 1e3
        total_durs += dur_us
        kname = args.get("KernelName", "")
        # Copy kernels carry "copyBuffer" or "copyMemory"
        if "copyBuffer" in kname or "copyMemory" in kname:
            io_durs += dur_us
        # Only kernel dispatches have these regs/LDS fields
        for fld in ("arch_vgpr", "sgpr", "lds"):
            if fld in args:
                acc[fld].append(int(args[fld]))
        if "wgr" in args:
            acc["block"].append(int(args["wgr"]))
        if "grd" in args:
            acc["grid"].append(int(args["grd"]))
    out = {}
    for k, v in acc.items():
        if v:
            out_key = (
                "avg_vgpr" if k == "arch_vgpr" else
                "avg_sgpr" if k == "sgpr" else
                "avg_lds"  if k == "lds" else
                "block_size" if k == "block" else
                "grid_size"
            )
            out[out_key] = statistics.mean(v)
    if total_durs:
        out["io_pct"] = io_durs / total_durs
    return out
def _parse_sysinfo(sys_path: Path) -> dict[str, int]:
    """
    Pull static GPU caps (Wavefront, Work-group size) from sysinfo.txt.
    """
    txt = sys_path.read_text()
    def grab(patt):
        m = re.search(patt, txt)
        return int(m.group(1)) if m else None
    return {
        "wavefront":   grab(r"Wavefront Size:\s+(\d+)"),
        "wg_max_size": grab(r"Workgroup Max Size:\s+(\d+)"),
    }
# ----------------------------------------------------------------------
# public
# ----------------------------------------------------------------------
def profile(bin_path: str) -> dict:
    """
    Compile-time + run-time profiling helper.
    Returns a dict with latency + occupancy + IO metrics.
    """
    with tempfile.TemporaryDirectory() as td:
        paths   = _run_rocprof(bin_path, td)
        durs_us = _parse_stats_csv(paths["stats_csv"])
        trace   = _parse_trace_json(paths["json"])
        caps    = _parse_sysinfo(paths["sysinfo"])
    stats = {
        "bin_path"  : bin_path,
        "n_kernels" : len(durs_us),
        "avg_us"    : statistics.mean(durs_us) if durs_us else None,
        "p95_us"    : (statistics.quantiles(durs_us, n=20)[18]
                       if len(durs_us) > 1 else None),
        **trace,
        **caps,
    }
    log.append({"event": "profile", **stats})
    return stats