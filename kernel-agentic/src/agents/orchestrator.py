from __future__ import annotations
import argparse, pathlib, yaml, json, time, shutil, os
from pathlib import Path
from typing import Dict, Any
from .torch_analyser import TorchAnalyser
from .baseline import _baseline_latency
from .rag_researcher import RAGResearcher
from .kernel_generator import KernelGenerator
from .executor import Executor
from ..utils.logger import log
from ..utils.kernel_compiler import compile_kernel
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

    torch_path = Path(torch_file)
    run_dir    = LOG_DIR / torch_path.stem
    run_dir.mkdir(exist_ok=True)

    # ---- stopping config ---------------------------------------------------
    min_iters      = STOP_CFG["min_iters"]
    max_iters      = iterations if iterations is not None else STOP_CFG["max_iters"]
    target_speedup = STOP_CFG["target_speedup"]
    eps            = STOP_CFG["min_improvement"]
    patience_phase1= STOP_CFG["patience"]         # for LLM phase
    max_fail       = STOP_CFG["max_failures"]

    # ---- helpers -----------------------------------------------------------
    torch_code = torch_path.read_text()
    
    # Get kernel language from config
    kernel_lang = PIPELINE_CFG.get("kernel_lang", "hip").lower()
    
    analyser   = TorchAnalyser()
    researcher = RAGResearcher(kernel_lang=kernel_lang)  # Language-specific RAG
    searcher   = SearchAgent()
    generator  = KernelGenerator(kernel_lang=kernel_lang)  # Language-specific generator
    runner     = Executor(kernel_lang=kernel_lang)  # Language-specific executor

    torch_expl = analyser.analyse(torch_code)
    baseline_us= _baseline_latency(torch_file, n_trial=2)

    best_code: str | None   = None
    best_us                 = float("inf")
    best_stats: Dict[str,Any]|None = None

    i, no_gain, fails = 0, 0, 0
    feedback = ""
    previous_kernel = ""  # Track previous kernel for iterations 2+

    # ---------- build context -------------------------------------------
    doc_ctx    = researcher.query(torch_expl) if PIPELINE_CFG['rag_enabled'] else ''
    search_ctx = searcher.search(torch_expl)  if PIPELINE_CFG['online_search'] else ''
    
    full_ctx= ""

    if PIPELINE_CFG['rag_enabled']:
        full_ctx  = full_ctx + "\n\n[Documentation Context]\n" + doc_ctx
    if PIPELINE_CFG['online_search']:
        full_ctx   = full_ctx + "\n\n[Internet Search Results]\n" + search_ctx

    while True:
        log.append({"event": "iteration_start", "iter": i})

        user_prompt = generator._build_user_prompt(torch_expl + "\n\n [Here is the PyTorch Code:] \n\n" + torch_code, full_ctx, feedback, previous_kernel)
        kernel_code = generator.generate(torch_expl + "\n\n [Here is the PyTorch Code:]  \n\n" + torch_code, full_ctx, feedback=feedback, iter_idx=i, previous_kernel=previous_kernel)
        # ---------- compile & run -------------------------------------------
        try:
            stats, errors, kernel_file = runner.run(kernel_code)
            print('*'*120)
            print('stats:', stats)
            print('errors:', errors)
            print('kernel_file:', kernel_file)
            print('*'*120)

            if CHK_NUM and not errors:
                err = max_abs_err(torch_file, kernel_file)
                errors = "" if err <= ATOL else f"MAX_ABS_ERR={err:.4e} > {ATOL}"
            fails = 0
        except Exception as exc:
            fails += 1
            feedback = str(exc)[:4000]
            log.append({"event": "iteration_failed", "iter": i, "error": feedback})
            if fails >= max_fail or i + 1 >= max_iters:
                log.append({"event": "early_stop",
                            "reason": "too_many_failures" if fails >= max_fail else "max_iters"})
                return
            i += 1
            continue

        # ---------- metrics -------------------------------------------------
        hip_us_raw = _extract_latency_us(stats)
        hip_us     = hip_us_raw if hip_us_raw is not None else float("inf")
        speedup    = baseline_us / hip_us if hip_us != float("inf") else 0.0
        correct    = errors == ""
        # ---------- phase-1 stop conditions ---------------------------------
        # reached_target  = speedup >= target_speedup and correct
        # out_of_patience = no_gain >= patience_phase1 and i + 1 >= min_iters
        # hit_max_iters   = i + 1 >= max_iters
        # if reached_target or out_of_patience or hit_max_iters:
        #     reason = ("target_speedup" if reached_target else
        #               "no_improvement" if out_of_patience else "max_iters")
        #     log.append({"event": "early_stop", "reason": reason,
        #                 "iter": i, "speedup": speedup})
        #     break
        reached_target  = correct and (speedup >= target_speedup)
        out_of_patience = correct and (no_gain   >= patience_phase1) and (i + 1 >= min_iters)
        # stop on max iters *regardless* of correctness
        hit_max_iters   = (i + 1) >= max_iters

        if reached_target or out_of_patience or hit_max_iters:
            reason = (
                "target_speedup"    if reached_target
                else "no_improvement" if out_of_patience
                else "max_iters"
            )
            log.append({
                "event" : "early_stop",
                "reason": reason,
                "iter"  : i,
                "speedup": speedup,
                "correct": correct
            })
            break

        # ---------- keep a correct kernel? ----------------------------------
        if correct:
            breakpoint()
            best_code  = Path(kernel_file).read_text()
            best_us    = hip_us
            best_stats = {"iter": i, "stats": stats,
                          "speedup": speedup, "baseline_us": baseline_us}
            
            # Get kernel language from config to set proper file extension
            kernel_lang = PIPELINE_CFG.get("kernel_lang", "hip").lower()
            extension_map = {"hip": ".hip", "cuda": ".cu", "triton": ".py"}
            ext = extension_map.get(kernel_lang, ".hip")
            
            shutil.copy(kernel_file, run_dir / f"{torch_path.stem}_iter{i}{ext}")
            log.append({                         # SFT sample (phase-1)
                "event"   : "sft_sample_phase1",
                "prompt"  : torch_code,
                "response": kernel_code,
                "iter"    : i,
                "speedup" : speedup,
                "hip_us"  : hip_us_raw,
            })
            log.append({"event":"phase1_complete","iter":i,"hip_us":hip_us})
            break   # ───────  exit phase-1 ➜ phase-2  ───────

        # bookkeeping for ‘no‐gain’
        if hip_us_raw is not None:
            improved = (best_us - hip_us) / best_us >= eps
            no_gain  = 0 if improved else no_gain + 1
        else:
            no_gain += 1

        log.append({                       # iteration logging (unchanged)
            "event"    : "iteration_complete",
            "iter"     : i,
            "correct"  : correct,
            "speedup"  : speedup,
            "hip_us"   : hip_us_raw,
            "prompt"   : user_prompt,
            "response" : kernel_code,
            **(stats or {})
        })
        # feedback = json.dumps({"profile": stats, "correct": correct,"errors": errors})[:8000]
        if errors:                                   
            feedback = errors                      
        else:                                        
            feedback = json.dumps({"profile": stats,"correct": True})[:8000]
        
        # Store current kernel as previous for next iteration  
        previous_kernel = kernel_code
        
        i += 1

    # ----------------  no correct kernel → abort whole run ------------------
    if best_code is None:
        log.append({"event": "no_valid_kernel", "torch": torch_file})
        return
    
    # ============================  PHASE 2 – HPO  ===============================
    #  optimiser selection
    op_type = analyser.classify(torch_expl)
    if op_type == "elem":
        search_space = {"block_size":[64,128,256,512]}
    elif op_type == "reduce":
        search_space = {"block_size":[64,128,256],
                        "vector_width":[1,2,4]}
    else:  # gemm/conv
        search_space = {"block_size":[128,256,512],
                        "tile_m":[8,16,32,64],
                        "tile_n":[8,16,32,64]}
                        
    if SEARCH_CFG.get("method", "bayes") == "bayes":
        optimiser = BayesOpt(
            max_trials=SEARCH_CFG.get("max_trials", 50),
            space=search_space           
        )
    else:
        optimiser = GeneticOpt(
            pop_size=SEARCH_CFG.get("pop", 16),
            ngen=SEARCH_CFG.get("ngen", 20),
            space=search_space          
        )
    breakpoint()
    no_gain_hpo = 0
    while True:
        sample = optimiser.next_params()
        if sample is None:
            log.append({"event":"early_stop_hpo","reason":"budget_exhausted"})
            break

        tunables = {k: v for k, v in sample.items() if not k.startswith("_")}
        patched_code = _apply_tunables(best_code, tunables)

        try:
            stats, _, kernel_file = runner.run(patched_code)
        except Exception as exc:
            log.append({"event":"hpo_compile_fail",
                        "params":tunables,"err":str(exc)})
            optimiser.update(sample.get("_trial") or sample.get("_ind"), 0.0)
            continue

        hip_us_raw = _extract_latency_us(stats)
        hip_us     = hip_us_raw if hip_us_raw is not None else float("inf")
        speedup    = baseline_us / hip_us if hip_us != float("inf") else 0.0

        optimiser.update(sample.get("_trial") or sample.get("_ind"), speedup)
        log.append({"event":"hpo_step","params":tunables,
                    "hip_us":hip_us_raw,"speedup":speedup})
        improved = hip_us < best_us and hip_us_raw is not None
        if improved:
            best_us   = hip_us
            best_code = patched_code
            best_stats= {"params":tunables,"stats":stats,
                         "speedup":speedup,"baseline_us":baseline_us}
            
            # Get proper file extension for current kernel language
            kernel_lang = PIPELINE_CFG.get("kernel_lang", "hip").lower()
            extension_map = {"hip": ".hip", "cuda": ".cu", "triton": ".py"}
            ext = extension_map.get(kernel_lang, ".hip")
            
            shutil.copy(kernel_file, run_dir / f"{torch_path.stem}_HPO_{int(speedup*100):03}{ext}")
            log.append({                 # SFT sample (phase-2)
                "event"   : "sft_sample_hpo",
                "prompt"  : torch_code,
                "response": patched_code,
                "params"  : tunables,
                "speedup" : speedup,
                "hip_us"  : hip_us_raw,
            })
            no_gain_hpo = 0
        else:
            no_gain_hpo += 1

        # ---- Phase-2 stop conditions --------------------------------------
        reached_target_hpo  = speedup >= target_speedup
        out_of_patience_hpo = no_gain_hpo >= SEARCH_CFG.get("patience", 10)
        if reached_target_hpo or out_of_patience_hpo:
            reason = ("target_speedup" if reached_target_hpo else "no_improvement")
            log.append({"event":"early_stop_hpo","reason":reason,"speedup":speedup})
            break
    breakpoint()
    # =========================================================================
    #  final persistence
    # =========================================================================
    if best_code:
        # Get proper file extension for current kernel language
        kernel_lang = PIPELINE_CFG.get("kernel_lang", "hip").lower()
        extension_map = {"hip": ".hip", "cuda": ".cu", "triton": ".py"}
        ext = extension_map.get(kernel_lang, ".hip")
        
        best_path = run_dir / f"{torch_path.stem}_best{ext}"
        best_path.write_text(best_code)
        with best_path.with_suffix(".json").open("w") as fp:
            json.dump(best_stats, fp, indent=2)
        log.append({"event":"best_kernel_saved","file":str(best_path),**best_stats})
    else:
        log.append({"event":"no_valid_kernel","torch":torch_file})

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--torch-file", default="input_torch.py", dest="torch_file")
    ap.add_argument("--iterations", type=int, default=None,
                    help="force fixed iteration count (skip auto-stop)")
    args = ap.parse_args()
    orchestrate(args.torch_file, args.iterations)
