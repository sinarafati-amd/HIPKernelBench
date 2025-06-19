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
from .search_agent import SearchAgent

from collections import deque
from ..eval.correctness import max_abs_err
from src.optim.bayes   import BayesOpt
from src.optim.genetic import GeneticOpt

CFG = yaml.safe_load(open("config.yml"))
STOP_CFG   = CFG["stopping"]
PIPELINE_CFG    = CFG.get("Pipeline", {})
EVAL_CFG    = CFG.get("eval", {})
HIP_CFG     = CFG.get("hip",  {})
LOG_DIR   = pathlib.Path("logs")
LOG_DIR.mkdir(exist_ok=True)
SEARCH_CFG = PIPELINE_CFG['search']
ATOL     = EVAL_CFG.get("atol", 1e-3)
CHK_NUM    = PIPELINE_CFG.get("enable_correctness", False) 

def _apply_tunables(code: str, params: Dict[str, Any]) -> str:
    patched = code
    for k, v in params.items():
        placeholder = f"/*{k.upper()}*/"
        patched = patched.replace(placeholder, str(v))
    return patched


def _extract_latency_us(stats: Dict[str, Any] | None) -> float | None:
    """
    Return avg_us from stats or None if missing/NaN/zero.
    """
    if not stats:
        return None
    val = stats.get("avg_us")
    try:
        return float(val) if val and val > 0 else None
    except (TypeError, ValueError):
        return None

def orchestrate(torch_file: str, iterations: int | None):

    torch_path   = Path(torch_file)
    run_dir      = LOG_DIR / torch_path.stem
    run_dir.mkdir(exist_ok=True)

    # stopping cfg
    min_iters      = STOP_CFG["min_iters"]
    max_iters      = iterations if iterations is not None else STOP_CFG["max_iters"]
    target_speedup = STOP_CFG["target_speedup"]
    eps            = STOP_CFG["min_improvement"]
    patience       = STOP_CFG["patience"]
    max_fail       = STOP_CFG["max_failures"]
    
    # pipeline helpers
    torch_code  = torch_path.read_text()
    analyser    = TorchAnalyser()
    researcher  = RAGResearcher()
    searcher    = SearchAgent()
    generator   = HIPGenerator()
    runner      = Executor()

    torch_expl  = analyser.analyse(torch_code)
    
    # best-so-far trackers
    best_code, best_stats = None, None
    best_us   = float("inf")

    # loop trackers
    no_gain, fails, i = 0, 0, 0
    feedback = ""
    baseline_us = _baseline_latency(torch_file, n_trial=2)

    # ================================  PHASE 1  =================================
    while True:
        log.append({"event": "iteration_start", "iter": i})

        search_ctx, doc_ctx = '', ''
        if PIPELINE_CFG['rag_enabled']:
            doc_ctx  = researcher.query(torch_expl)
        if PIPELINE_CFG['online_search']:
            search_ctx = searcher.search(torch_expl)
        
        full_ctx   = doc_ctx + "\n\n## Internet Search Results ##\n" + search_ctx
        user_prompt = generator._build_user_prompt(torch_expl + "\n\n" + torch_code,full_ctx,feedback)
        hip_code = generator.generate(torch_expl + "\n\n" + torch_code, full_ctx, feedback=feedback, iter_idx=i)
        
        try:
            stats, errors, hip_file = runner.run(hip_code)    
            # check for tolerance error 
            if CHK_NUM and not errors:
                err = max_abs_err(torch_file, hip_file)
                if err > ATOL:
                    errors = f"MAX_ABS_ERR={err:.4e} > {ATOL}"
                else:
                    errors = ""  

            fails = 0
        except Exception as exc:
            fails += 1
            feedback = str(exc)[:4000]
            log.append({"event": "iteration_failed", "iter": i, "error": feedback})

            if fails >= max_fail or i + 1 >= max_iters:
                log.append({"event": "early_stop",
                            "reason": "too_many_failures" if fails>=max_fail else "max_iters"})
                break
            i += 1
            continue

        hip_us_raw = _extract_latency_us(stats)
        hip_us     = hip_us_raw if hip_us_raw is not None else float("inf")
        speedup    = baseline_us / hip_us if hip_us != float("inf") else 0.0
        # correct = not bool(errors)
        correct    = errors == ""

        # keep fastest correct kernel
        if correct and hip_us_raw is not None and hip_us < best_us:
            best_us   = hip_us
            best_code = Path(hip_file).read_text()
            best_stats = {"iter": i, "stats": stats,
                          "speedup": speedup, "baseline_us": baseline_us}
            shutil.copy(hip_file, run_dir / f"{torch_path.stem}_iter{i}.hip")
            shutil.copy(hip_file, run_dir / f"{torch_path.stem}_iter{i}.cpp")

            log.append({
                "event"   : "sft_sample",
                "prompt"  : torch_code,   
                "response": hip_code,     
                "iter"    : i,
                "correct"  : correct,
                "speedup" : speedup,
                "hip_us"  : hip_us_raw,
            })


        else:
            shutil.copy(hip_file, run_dir / f"FAILED_{torch_path.stem}_iter{i}.hip")

        # improvement bookkeeping
        if hip_us_raw is not None:
            improved = (best_us - hip_us) / best_us >= eps
            no_gain  = 0 if improved else no_gain + 1
        else:
            no_gain += 1

        # ---------------- stop-conditions ----------------------------------
        reached_target  = speedup >= target_speedup and correct
        out_of_patience = no_gain >= patience and i + 1 >= min_iters
        hit_max_iters   = i + 1 >= max_iters

        if reached_target or out_of_patience or hit_max_iters:
            reason = ("target_speedup" if reached_target else
                      "no_improvement" if out_of_patience else
                      "max_iters")
            log.append({"event": "early_stop", "reason": reason,
                        "iter": i, "speedup": speedup})
            # fall through to iteration_complete logging before break
            terminate = True
        else:
            terminate = False

        # ---------------- iteration-complete log ---------------------------
        log.append({
            "event"    : "iteration_complete",
            "iter"     : i,
            "correct"  : correct,
            "speedup"  : speedup,
            "hip_us"   : hip_us_raw,
            "prompt"   : user_prompt, 
            "response" : hip_code,    
             **(stats or {})
        })

        feedback = json.dumps({"profile": stats, "correct": correct, "errors": errors})[:6000]
        if terminate:
            break

        i += 1
    # ------------------------------------------------------------------
    #  Finalise – persist the SINGLE best kernel (if any)
    # ------------------------------------------------------------------
    if best_code:
        best_path = run_dir / f"{torch_path.stem}_best.hip"
        best_path.write_text(best_code)
        with best_path.with_suffix(".json").open("w") as fp:
            json.dump(best_stats, fp, indent=2)
        log.append({"event": "best_kernel_saved", "file": str(best_path),
                    **best_stats})
    else:
        log.append({"event": "no_valid_kernel", "torch": torch_file})
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--torch-file", default="input_torch.py", dest="torch_file")
    ap.add_argument("--iterations", type=int, default=None,
                    help="force fixed iteration count (skip auto-stop)")
    args = ap.parse_args()
    orchestrate(args.torch_file, args.iterations)
