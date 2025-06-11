from __future__ import annotations
import argparse, pathlib, yaml, json, time, shutil, os
from pathlib import Path
from typing import Dict, Any
from .torch_analyser import TorchAnalyser
from .baseline import _baseline_latency
from .rag_researcher import RAGResearcher
from .hip_generator import HIPGenerator
from .executor import Executor
from ..utils.logger import log
from ..utils.hip_compiler import compile_hip
from ..utils.rocprof_parser import profile
from ..eval.timing      import time_fn
from ..eval.fastp       import fast_p


CFG = yaml.safe_load(open("config.yml"))
EVAL_CFG    = CFG.get("eval", {})
HIP_CFG     = CFG.get("hip",  {})
LOG_DIR   = pathlib.Path("logs")
LOG_DIR.mkdir(exist_ok=True)

def orchestrate(torch_file: str, iterations: int):

    torch_path  = Path(pathlib.Path(torch_file))
    output_path = Path(os.path.join('logs',torch_path.name.split('.')[0]))
    output_path.mkdir(exist_ok=True)
    
    torch_code = pathlib.Path(torch_file).read_text()
    analyser = TorchAnalyser()
    researcher = RAGResearcher()
    generator  = HIPGenerator()
    runner     = Executor()
    
    torch_expl = analyser.analyse(torch_code)
    baseline_us = _baseline_latency(torch_file,n_trial=5)
    
    
    # ------------------------------------------------------------------
    # Best-so-far tracker
    # ------------------------------------------------------------------
    best_code      : str  | None = None
    best_us        : float        = float("inf")
    best_stats     : Dict[str, Any] | None = None
    best_bin_path  : pathlib.Path | None = None

    feedback    = ""   # compile/runtime feedback loop
    
    for i in range(iterations):

        log.append({"event": "iteration_start", "iter": i})
        doc_ctx  = researcher.query(torch_expl)
        hip_code = generator.generate(torch_expl+"\n\n"+torch_code, doc_ctx, feedback=feedback)

        try:
            stats, errors, hip_file= runner.run(hip_code)
            correct = not bool(errors)
            compiled_code=None
            hip_us = stats.get("avg_us") or 1e9
            speedup= baseline_us / hip_us

            # ----------------------------------------------------------
            # Keep only *valid* and *fastest* kernel
            # ----------------------------------------------------------
            if correct and hip_us < best_us:
                compiled_code = pathlib.Path(hip_file).read_text(encoding="utf-8")
                best_us    = hip_us
                best_code  = compiled_code
                best_stats = {"iter": i, "stats": stats,
                              "speedup": speedup, "baseline_us": baseline_us}
                iter_cpp = LOG_DIR / torch_path.name.split('.')[0] /f"{torch_path.stem}_iter{i}.cpp"
                shutil.copy(hip_file, iter_cpp)


            log.append({
                "event"      : "iteration_complete",
                "iter"       : i,
                "hip_code"   : compiled_code,
                "correct"    : correct,
                "speedup"    : speedup,
                **stats
            })
            feedback = json.dumps({"profile": stats, "correct": correct, "errors": errors})
        except Exception as e:
            fb = str(e)[:4000]
            log.append({"event": "iteration_failed", "iter": i, "error": fb})
            feedback = fb
            continue
    # ------------------------------------------------------------------
    #  Finalise – persist the SINGLE best kernel (if any)
    # ------------------------------------------------------------------
    if best_code:
        hip_path = LOG_DIR/f"{torch_path.stem}" / f"{torch_path.stem}_best.hip"
        hip_path.write_text(best_code)
        with open(hip_path.with_suffix(".json"), "w") as fp:
            json.dump(best_stats, fp, indent=2)
        log.append({"event": "best_kernel_saved",
                    "file": str(hip_path),
                    **best_stats})
    else:
        log.append({"event": "no_valid_kernel", "torch": torch_file})
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--torch-file", default="input_torch.py", dest="torch_file")
    ap.add_argument("--iters", type=int, default=CFG["hip"]["max_iters"], dest="iterations")
    args = ap.parse_args()
    orchestrate(args.torch_file, args.iterations)
