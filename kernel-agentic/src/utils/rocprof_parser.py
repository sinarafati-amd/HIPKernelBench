import csv, subprocess, tempfile, os, statistics
from pathlib import Path
from .logger import log


def _run_rocprof(bin_path: str, workdir: str) -> Path:
    """
    Launch rocprof in --stats mode and return the '*.stats.csv' file path.
    """
    prefix = Path(workdir) / "results"          # rocprof will append suffixes
    prefix.mkdir(parents=True, exist_ok=True)
    cmd = ["rocprof","--stats","--basenames", "on","-o", str(os.path.join(prefix,"results.csv")), bin_path]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        # capture stderr to logs so debugging is easier
        log.append({"event": "profile_error",
                    "stderr": e.stderr,
                    "stdout": e.stdout})
        raise RuntimeError(f"rocprof failed (exit {e.returncode})")

    stats_file = prefix / 'results.stats.csv'
    if not stats_file.exists():
        raise FileNotFoundError(f"rocprof did not create {stats_file}")
    return stats_file


def _parse_stats_csv(csv_path: Path) -> list[float]:
    """
    Return list of durations (μs) for every kernel in .stats.csv.
    """
    durs_us = []
    with csv_path.open() as fp:
        reader = csv.DictReader(fp)
        for row in reader:
            try:
                d_ns = float(row["AverageNs"])
                durs_us.append(d_ns / 1e3)          # convert → μs
            except (KeyError, ValueError):
                continue
    return durs_us


def profile(bin_path: str) -> dict:
    """
    Compile-time + run-time profiling helper.
    Returns: {
        "bin_path": …,
        "n_kernels": int,
        "avg_us": float | None,
        "p95_us": float | None
    }
    """
    with tempfile.TemporaryDirectory() as td:
        csv_path  = _run_rocprof(bin_path, td)
        durs_us   = _parse_stats_csv(csv_path)

    stats = {
        "bin_path"  : bin_path,
        "n_kernels" : len(durs_us),
        "avg_us"    : statistics.mean(durs_us) if durs_us else None,
        "p95_us"    : (statistics.quantiles(durs_us, n=20)[18]
                       if len(durs_us) > 1 else None),
    }
    log.append({"event": "profile", **stats})
    return stats
