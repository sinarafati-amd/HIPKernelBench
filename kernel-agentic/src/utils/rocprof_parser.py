from __future__ import annotations
import pandas as pd
import numpy as np
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

DEFAULT_WEIGHTS = {
    # Ops-Intensity
    "L1 AI (FLOPs/Byte)"  : 0.0667,
    "L2 AI (FLOPs/Byte)"  : 0.0667,
    "HBM AI (FLOPs/Byte)" : 0.0667,
    # Ops-Throughput
    "L1 GFLOP/S" : 0.10,
    "L2 GFLOP/S" : 0.10,
    "HBM GFLOP/S": 0.10,
    # 带宽
    "vL1D Cache BW" : 0.05,
    "L2 Cache BW"   : 0.05,
    "HBM BW"        : 0.05,
    # 命中率
    "vL1D Cache Hit Rate" : 0.05,
    "L2 Cache Hit Rate"   : 0.05,
    # Fabric
    "L2-Fabric Read BW"  : 0.05,
    "L2-Fabric Write BW" : 0.05,
}

rocprof_pmc_column_desc = {
    # ---- Dispatch-level meta ----
    "Grid_Size": "Total number of threads in the grid.",
    "Workgroup_Size": "Number of threads per work-group (block).",
    "LDS_Per_Workgroup": "Bytes of LDS (shared memory) statically allocated per work-group.",
    "Scratch_Per_Workitem": "Private scratch memory bytes spilled per work-item.",
    "Arch_VGPR": "Architected vector registers used per thread.",
    "Accum_VGPR": "Additional accumulator VGPRs used by MFMA operations.",
    "SGPR": "Scalar registers used per wavefront.",
    "wave_size": "Active threads per wavefront (typically 64 on CDNA3).",
    # ---- LDS efficiency ----
    "SQ_LDS_IDX_ACTIVE": "Cycles during which indexed LDS access was active.",
    "SQ_LDS_BANK_CONFLICT": "Cycles stalled due to LDS bank conflicts.",
    # ---- Duplicate meta from other counter passes ----
    "wave_size_1": "Duplicate wave_size column from a second counter pass.",
    # ---- F64 VALU instruction mix ----
    "SQ_INSTS_VALU_ADD_F64": "Number of 64-bit floating add/sub instructions.",
    "SQ_INSTS_VALU_MUL_F64": "Number of 64-bit floating multiply instructions.",
    "SQ_INSTS_VALU_FMA_F64": "Number of 64-bit fused multiply-add instructions.",
    "SQ_INSTS_VALU_TRANS_F64": "Number of 64-bit transcendental instructions.",
    # ---- MFMA (Tensor Core) micro-ops ----
    "SQ_INSTS_VALU_MFMA_MOPS_F16": "MFMA micro-ops executed on FP16 data.",
    "SQ_INSTS_VALU_MFMA_MOPS_BF16": "MFMA micro-ops executed on BF16 data.",
    "SQ_INSTS_VALU_MFMA_MOPS_F32": "MFMA micro-ops executed on FP32 data.",
    "SQ_INSTS_VALU_MFMA_MOPS_F64": "MFMA micro-ops executed on FP64 data.",
    # ---- L1→L2 atomic & bubble ----
    "TCP_TCC_ATOMIC_WITHOUT_RET_REQ_sum": "Atomic requests without return value sent from L1 (TCP) to L2 (TCC).",
    "TCC_BUBBLE_sum": "Idle cycles (bubbles) observed in the L2 cache pipeline.",
    # ---- More duplicate meta ----
    "wave_size_2": "Duplicate wave_size column from a third counter pass.",
    # ---- F16 VALU instruction mix ----
    "SQ_INSTS_VALU_ADD_F16": "Number of 16-bit floating add/sub instructions.",
    "SQ_INSTS_VALU_MUL_F16": "Number of 16-bit floating multiply instructions.",
    "SQ_INSTS_VALU_FMA_F16": "Number of 16-bit fused multiply-add instructions.",
    "SQ_INSTS_VALU_TRANS_F16": "Number of 16-bit transcendental instructions.",
    # ---- F32 VALU instruction mix ----
    "SQ_INSTS_VALU_ADD_F32": "Number of 32-bit floating add/sub instructions.",
    "SQ_INSTS_VALU_MUL_F32": "Number of 32-bit floating multiply instructions.",
    "SQ_INSTS_VALU_FMA_F32": "Number of 32-bit fused multiply-add instructions.",
    "SQ_INSTS_VALU_TRANS_F32": "Number of 32-bit transcendental instructions.",
    # ---- L1/TCP cache traffic ----
    "TCP_TCC_READ_REQ_sum": "Total read requests sent from L1 (TCP) to L2 (TCC).",
    "TCP_TOTAL_CACHE_ACCESSES_sum": "Total L1 data-cache accesses for this dispatch.",
    "TCP_TCC_WRITE_REQ_sum": "Total write requests sent from L1 (TCP) to L2 (TCC).",
    "TCP_TCC_ATOMIC_WITH_RET_REQ_sum": "Atomic requests with return value sent from L1 (TCP) to L2 (TCC).",
    # ---- External-memory traffic seen at L2 bank 0 ----
    "TCC_EA0_RDREQ_32B_sum": "32-byte external memory read requests at L2 bank 0.",
    "TCC_EA0_RDREQ_sum": "All external memory read requests at L2 bank 0.",
    "TCC_EA0_WRREQ_64B_sum": "64-byte external memory write requests at L2 bank 0.",
    "TCC_EA0_WRREQ_sum": "All external memory write requests at L2 bank 0.",
}

def collect_metrics(run_dir: str | Path) -> pd.DataFrame:
    """
    Read four rocprof-compute CSV files and calculate for each kernel:
       - AI (FLOPs / Byte) 3 items
       - GFLOP/S           3 items
       - Bandwidth         3 items
       - Cache Hit Rate    2 items
       - Fabric Bandwidth  2 items
    Returns: DataFrame with Dispatch_ID
    """
    
    run_dir = Path(run_dir)

    # ---- Basic tables ----
    df = pd.read_csv(run_dir / "pmc_perf.csv")
    sysinfo   = pd.read_csv(run_dir / "sysinfo.csv")
    roofline  = pd.read_csv(run_dir / "roofline.csv")   # Only uses wave_size=64 constant, can skip reading

    raw_pmc_dict = {}

    for key, value in rocprof_pmc_column_desc.items():
        tmp_key = value
        tmp_value = df[key].iloc[0]
        if isinstance(tmp_value, (np.generic, np.ndarray)):
            tmp_value = tmp_value.item()
        raw_pmc_dict[tmp_key] = tmp_value

    df["raw_pmc_str"] = str(raw_pmc_dict)

    # ---- Core fusion ----
    df["duration_s"] = (df.End_Timestamp - df.Start_Timestamp) * 1e-9   # ns → s
    wave = 64                                                           # AMD fixed

    # ---- (1) Estimate total FLOPs (including multiple precisions) ----
    df["FLOPs"] = (
        # FP32 operations
        (df.SQ_INSTS_VALU_ADD_F32 + df.SQ_INSTS_VALU_MUL_F32) * wave +
        df.SQ_INSTS_VALU_FMA_F32 * wave * 2 +
        # FP16 operations  
        (df.SQ_INSTS_VALU_ADD_F16 + df.SQ_INSTS_VALU_MUL_F16) * wave +
        df.SQ_INSTS_VALU_FMA_F16 * wave * 2 +
        # FP64 operations
        (df.SQ_INSTS_VALU_ADD_F64 + df.SQ_INSTS_VALU_MUL_F64) * wave +
        df.SQ_INSTS_VALU_FMA_F64 * wave * 2 +
        # MFMA operations (Matrix Fused Multiply-Add)
        df.SQ_INSTS_VALU_MFMA_MOPS_F16 * 16 +  # F16 MFMA typically 16 ops per instruction
        df.SQ_INSTS_VALU_MFMA_MOPS_BF16 * 16 + # BF16 MFMA typically 16 ops per instruction  
        df.SQ_INSTS_VALU_MFMA_MOPS_F32 * 4 +   # F32 MFMA typically 4 ops per instruction
        df.SQ_INSTS_VALU_MFMA_MOPS_F64 * 1     # F64 MFMA typically 1 op per instruction
    )

    # ---- (2) Bytes: L1 / L2 / HBM ----
    # L1 cache: total cache accesses * cache line size (typically 64 bytes)
    df["bytes_L1"] = df.TCP_TOTAL_CACHE_ACCESSES_sum * 64
    # L2 cache: read/write requests * cache line size (typically 64 bytes)
    df["bytes_L2"] = (df.TCP_TCC_READ_REQ_sum + df.TCP_TCC_WRITE_REQ_sum) * 64
    # HBM: actual transfer sizes
    df["bytes_HBM"] = (
        df.TCC_EA0_RDREQ_32B_sum * 32 +  # 32-byte read requests
        df.TCC_EA0_WRREQ_64B_sum * 64    # 64-byte write requests
    )

    # ---- (3) Arithmetic Intensity ----
    df["L1 AI (FLOPs/Byte)"]  = df.FLOPs / df.bytes_L1.replace(0, np.nan)
    df["L2 AI (FLOPs/Byte)"]  = df.FLOPs / df.bytes_L2.replace(0, np.nan)
    df["HBM AI (FLOPs/Byte)"] = df.FLOPs / df.bytes_HBM.replace(0, np.nan)

    # ---- (4) Throughput (GFLOP/s) ----
    gflops = df.FLOPs / df.duration_s / 1e9
    df["L1 GFLOP/S"]  = gflops              # Give 3 identical values here for unified weight model
    df["L2 GFLOP/S"]  = gflops
    df["HBM GFLOP/S"] = gflops

    # ---- (5) Bandwidth (GB/s) ----
    df["vL1D Cache BW"] = df.bytes_L1 / df.duration_s / 1e9
    df["L2 Cache BW"]   = df.bytes_L2 / df.duration_s / 1e9
    df["HBM BW"]        = df.bytes_HBM / df.duration_s / 1e9

    # ---- (6) Hit Rate ----
    # vL1D Cache Hit Rate: 1 - (L2 read requests / total cache accesses)
    df["vL1D Cache Hit Rate"] = 1.0 - df.TCP_TCC_READ_REQ_sum / df.TCP_TOTAL_CACHE_ACCESSES_sum.replace(0, np.nan)
    # L2 Cache Hit Rate: 1 - (HBM read requests / L2 read requests) 
    df["L2 Cache Hit Rate"]   = 1.0 - df.TCC_EA0_RDREQ_32B_sum / df.TCP_TCC_READ_REQ_sum.replace(0, np.nan)

    # ---- (7) Fabric Bandwidth (GB/s) ----
    # Fabric bandwidth: L2 cache line transfers to/from fabric
    df["L2-Fabric Read BW"]  = df.TCP_TCC_READ_REQ_sum  * 64 / df.duration_s / 1e9  # 64-byte cache lines
    df["L2-Fabric Write BW"] = df.TCP_TCC_WRITE_REQ_sum * 64 / df.duration_s / 1e9  # 64-byte cache lines

    return df[["raw_pmc_str"] + list(DEFAULT_WEIGHTS.keys())]