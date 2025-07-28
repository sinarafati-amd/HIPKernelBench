from __future__ import annotations
import argparse, pathlib, yaml, json, time, shutil, os, re, base64, requests
from pathlib import Path
from typing import Dict, Any, Tuple, Callable

from .search_agent import SearchAgent
from .torch_analyser      import TorchAnalyser
from .feedback_analyzer   import KernelFeedbackAnalyser
from .baseline            import baseline_latency
from .rag_researcher      import RAGResearcher
from .kernel_generator    import KernelGenerator
from .kernel_analyser     import KernelAnalyser
from .kernel_optimizer    import KernelOptimizer
from .executor            import Executor
from ..utils.logger       import log
from ..eval.correctness   import max_abs_err
from src.optim.bayes      import BayesOpt
from src.optim.genetic    import GeneticOpt

SERVER_URL = "http://localhost:8081"

CFG          = yaml.safe_load(open("config.yml"))
STOP_CFG     = CFG["stopping"]
PIPELINE_CFG = CFG.get("Pipeline", {})
EVAL_CFG     = CFG.get("eval", {})
ATOL         = float(EVAL_CFG.get("atol", 1e-3))
HIP_CFG      = CFG.get("hip", {})
SEARCH_CFG   = PIPELINE_CFG['search']
CHK_NUM      = PIPELINE_CFG.get("enable_correctness", False)

LOG_DIR = pathlib.Path("logs")
LOG_DIR.mkdir(exist_ok=True)

def _apply_tunables(code: str, params: Dict[str, Any]) -> str:
    patched = code
    for k, v in params.items():
        placeholder = f"/*{k.upper()}*/"
        patched = patched.replace(placeholder, str(v))
    return patched


def _extract_latency_us(stats: Dict[str, Any] | None) -> float | None:
    if not stats:
        return None
    val = stats.get("avg_us")
    try:
        return float(val) if val and val > 0 else None
    except (TypeError, ValueError):
        return None

def _compile_and_profile(
        runner: Executor,
        kernel_code: str,
        kernel_lang: str
) -> Tuple[Dict[str, Any] | None, str | None, str | None]:
    """
    Compile & run a kernel and (optionally) call OmniViSe for extra stats.
    Returns: (stats, errors, produced_kernel_file)
    """
    stats, errors, kernel_file = runner.run(kernel_code)

    if errors in (None, "") and PIPELINE_CFG.get('omnivise', False):
        encoded_code  = base64.b64encode(kernel_code.encode("utf-8")).decode("utf-8")
        json_payload  = {
            "architecture": CFG[kernel_lang]['gpu_arch'],
            "compiler_flags": "-O3",
            "code": encoded_code
        }
        response = requests.post(
            SERVER_URL,
            headers={"Content-Type": "application/json"},
            data=json.dumps(json_payload)
        )
        if response.status_code == 200:
            omnivise_json = json.loads(response.text)
            stats.update(omnivise_json['data'])

    # Normalise error value to *always* be a string
    errors = "" if errors is None else errors
    return stats, errors, kernel_file

def _save_best_kernel(
        code: str,
        stats: Dict[str, Any],
        run_dir: Path,
        stem: str,
        tag: str,
        kernel_lang: str
) -> Path:
    """
    Persist the kernel (and companion JSON) to disk.  
    Returns path of the saved kernel file.
    """
    ext_map = {"hip": ".hip", "cuda": ".cu", "triton": ".py"}
    ext     = ext_map.get(kernel_lang, ".hip")

    out_path = run_dir / f"{stem}_{tag}{ext}"
    out_path.write_text(code)
    # duplicate as .cpp if hip / cuda so user can open quickly in IDE’s
    if kernel_lang in ("hip", "cuda"):
        shutil.copy(out_path, out_path.with_suffix(".cpp"))

    with out_path.with_suffix(".json").open("w") as fp:
        json.dump(stats, fp, indent=2)

    log.append({"event": "kernel_saved", "file": str(out_path), **stats})
    return out_path


def _run_hpo_phase2(
        *,
        initial_best_code: str,
        initial_best_us: float,
        baseline_us: float,
        op_type: str,
        runner: Executor,
        kernel_lang: str,
        run_dir: Path,
        stem: str
) -> Tuple[str, float, Dict[str, Any] | None]:
    """
    Common Phase-2 (Bayes / GA) hyper-parameter optimisation used by both
    pipelines.  Returns (best_code, best_us, best_stats)
    """
    # -------------------- build search-space -------------------------------
    if op_type == "elem":
        search_space = {"block_size": [64, 128, 256, 512, 32, 1024]}
    elif op_type == "reduce":
        search_space = {"block_size": [64, 128, 256], "vector_width": [1, 2, 4]}
    else:  # gemm / conv
        search_space = {
            "block_size": [128, 256, 512],
            "tile_m":     [8, 16, 32, 64],
            "tile_n":     [8, 16, 32, 64]
        }

    if SEARCH_CFG.get("method", "bayes") == "bayes":
        optimiser = BayesOpt(max_trials=SEARCH_CFG.get("max_trials", 50),
                             space=search_space)
    else:
        optimiser = GeneticOpt(pop_size=SEARCH_CFG.get("pop", 16),
                               ngen=SEARCH_CFG.get("ngen", 20),
                               space=search_space)

    best_code  = initial_best_code
    best_us    = initial_best_us
    best_stats = None
    no_gain_hpo = 0
    counter     = 0

    if not SEARCH_CFG.get('enabled', True):
        return best_code, best_us, best_stats

    while True:
        counter += 1
        print(f"Phase-2 HPO iteration {counter}  [{optimiser.__class__.__name__}]")

        trial = optimiser.next_params()
        if trial is None:
            log.append({"event": "early_stop_hpo", "reason": "budget_exhausted"})
            break

        tunables    = {k: v for k, v in trial.items() if not k.startswith("_")}
        patched     = _apply_tunables(best_code, tunables)

        try:
            stats, errs, kfile = _compile_and_profile(runner, patched, kernel_lang)
        except Exception as exc:
            optimiser.update(trial.get("_trial") or trial.get("_ind"), 0.0)
            log.append({"event": "hpo_compile_fail",
                        "params": tunables, "err": str(exc)})
            continue

        hip_us_raw = _extract_latency_us(stats)
        hip_us     = hip_us_raw if hip_us_raw is not None else float("inf")
        speedup    = baseline_us / hip_us if hip_us != float("inf") else 0.0

        optimiser.update(trial.get("_trial") or trial.get("_ind"), speedup)
        log.append({"event": "hpo_step", "params": tunables,
                    "hip_us": hip_us_raw, "speedup": speedup})

        improved = hip_us < best_us and hip_us_raw is not None
        if improved:
            best_code  = patched
            best_us    = hip_us
            best_stats = {"params": tunables, "stats": stats,
                          "speedup": speedup, "baseline_us": baseline_us}

            _save_best_kernel(best_code, best_stats, run_dir,
                              stem, f"HPO_{int(speedup * 100):03}", kernel_lang)
            no_gain_hpo = 0
        else:
            no_gain_hpo += 1

        # stop-conditions
        target_speedup    = STOP_CFG["target_speedup"]
        reached_target    = speedup >= target_speedup
        out_of_patience   = no_gain_hpo >= SEARCH_CFG.get("patience", 16)
        if out_of_patience:
            reason = "target_speedup" if reached_target else "no_improvement"
            log.append({"event": "early_stop_hpo", "reason": reason,
                        "speedup": speedup})
            break

    return best_code, best_us, best_stats


def _parse_torch_analysis(torch_expl_raw: str, kernel_lang: str):
    """
    Returns (explanation, cheat_code_for_LLM)
    """
    try:
        json_match = re.search(r'```json\n(.*?)\n```', torch_expl_raw, re.DOTALL)
        json_str   = json_match.group(1) if json_match else torch_expl_raw
        torch_analysis = json.loads(json_str)
        torch_expl = torch_analysis.get("explanation", "")
        kernels    = torch_analysis.get("top_kernels", [])
    except (json.JSONDecodeError, AttributeError):
        torch_expl, kernels = torch_expl_raw, []

    cheat_code  = ""
    sheets_dir  = Path(__file__).parent.parent / "sheets"
    if os.path.exists(sheets_dir) and kernels:
        kernel_sheet = sheets_dir / f"{kernel_lang}.json"
        with open(kernel_sheet) as fp:
            kernel_data = json.load(fp)

        cheat_lines = []
        for kname in kernels:
            for entry in kernel_data['kernels']:
                if entry.get('name') == kname:
                    cheat_lines.append(
                        f"{entry['name']}:\n"
                        f"   torch code:\n{entry['pytorch']}\n\n"
                        f"   kernel code:\n{entry['kernel']}\n")
                    break
        cheat_code = "\n".join(cheat_lines)
    return torch_expl, cheat_code

def _parse_kernel_analysis(analysis_raw: str):
    try:
        json_match = re.search(r'```json\n(.*?)\n```', analysis_raw, re.DOTALL)
        json_str   = json_match.group(1) if json_match else analysis_raw
        analysis   = json.loads(json_str)
        return (
            analysis.get("explanation", ""),
            analysis.get("operation_type", "other"),
            analysis.get("optimization_opportunities", []),
            analysis.get("current_bottlenecks", []),
            analysis.get("complexity_analysis", "")
        )
    except (json.JSONDecodeError, AttributeError):
        return analysis_raw, "other", [], [], ""

def orchestrate(torch_file: str, iterations: int | None):
    """
    End-to-end pipeline that converts PyTorch code into an optimised GPU kernel.
    The core compile/run/HPO mechanics now live in shared helper functions above.
    """

    torch_path = Path(torch_file)
    run_dir    = LOG_DIR / torch_path.stem
    run_dir.mkdir(exist_ok=True)

    min_iters       = iterations or STOP_CFG["min_iters"]
    max_iters       = iterations or STOP_CFG["max_iters"]
    patience_phase1 = STOP_CFG["patience"]
    target_speedup  = STOP_CFG["target_speedup"]
    eps             = STOP_CFG["min_improvement"]
    max_fail        = STOP_CFG["max_failures"]

    torch_code  = torch_path.read_text()
    kernel_lang = PIPELINE_CFG.get("kernel_lang", "hip").lower()

    analyser          = TorchAnalyser()
    feedback_analyzer = KernelFeedbackAnalyser()
    researcher        = RAGResearcher(kernel_lang=kernel_lang)
    searcher          = SearchAgent()
    generator         = KernelGenerator(kernel_lang=kernel_lang)
    runner            = Executor(kernel_lang=kernel_lang)

    torch_expl_raw          = analyser.analyse(torch_code)
    torch_expl, cheat_sheet = _parse_torch_analysis(torch_expl_raw, kernel_lang)

    baseline_us      = baseline_latency(torch_file, n_trial=2)
    best_code        = None
    best_us          = float("inf")
    best_stats       = None

    i = no_gain = fails = 0
    feedback            = ""
    previous_kernel     = ""

    # build static context for the LLM
    doc_ctx    = "\n\n[Documentation Context]\n" + researcher.query(torch_expl) \
                 if PIPELINE_CFG.get('rag_enabled') else ''
    search_ctx = "\n\n[Internet Search Results]\n" + searcher.search(torch_expl) \
                 if PIPELINE_CFG.get('online_search') else ''
    full_ctx   = doc_ctx + search_ctx

    # ───────────────────────── PHASE 1 : LLM code-gen loop ─────────────────
    while True:
        log.append({"event": "iteration_start", "iter": i})

        code_input  = torch_expl + "\n\n[PyTorch Code]\n" + torch_code
        if PIPELINE_CFG.get('cheat_sheet') and i == 0:
            code_input += "\n\n[Available Kernels]\n" + cheat_sheet

        kernel_code = generator.generate(code_input, full_ctx,
                                         feedback=feedback,
                                         iter_idx=i,
                                         previous_kernel=previous_kernel)

        # ---------- compile & run ------------------------------------------
        try:
            stats, errors, kernel_file = _compile_and_profile(runner, kernel_code,
                                                              kernel_lang)
            # Optional numeric-correctness check
            if CHK_NUM and errors == "":
                so_path = Path(kernel_file).with_name("kernel.so")
                if so_path.exists():
                    err_val = max_abs_err(torch_file, so_path)
                    errors = "" if err_val <= ATOL \
                                  else f"MAX_ABS_ERR={err_val:.4e} > {ATOL}"
                else:
                    errors = "Shared library not found for correctness check"
            fails = 0
        except Exception as exc:
            fails += 1
            feedback = str(exc)
            log.append({"event": "iteration_failed", "iter": i, "error": feedback})
            if fails >= max_fail or i + 1 >= max_iters:
                log.append({"event": "early_stop",
                            "reason": "too_many_failures"
                                      if fails >= max_fail else "max_iters"})
                return
            i += 1
            continue

        hip_us_raw = _extract_latency_us(stats)
        hip_us     = hip_us_raw if hip_us_raw is not None else float("inf")
        speedup    = baseline_us / hip_us if hip_us != float("inf") else 0.0
        correct    = errors == ""

        # --------------- stop-conditions -----------------------------------
        reached_target  = correct and speedup >= target_speedup
        out_of_patience = correct and (no_gain >= patience_phase1) \
                          and (i + 1 >= min_iters)
        hit_max_iters   = (i + 1) >= max_iters

        if reached_target or out_of_patience or hit_max_iters:
            reason = ("target_speedup" if reached_target
                      else "no_improvement" if out_of_patience
                      else "max_iters")
            log.append({"event": "early_log", "iter": i, "reason": reason,
                        "speedup": speedup, "correct": correct})

            if correct:
                best_code  = Path(kernel_file).read_text()
                best_us    = hip_us
                best_stats = {"iter": i, "stats": stats,
                              "speedup": speedup, "baseline_us": baseline_us}
                _save_best_kernel(best_code, best_stats, run_dir,
                                  torch_path.stem, f"iter{i}", kernel_lang)
                break  # leave phase-1

        # bookkeeping for “no gain”
        if hip_us_raw is not None:
            improved = (best_us - hip_us) / best_us >= eps
            no_gain  = 0 if improved else no_gain + 1
        else:
            no_gain += 1

        log.append({"event": "iteration_complete", "iter": i,
                    "correct": correct, "speedup": speedup, "hip_us": hip_us_raw,
                    "prompt": generator.build_user_prompt(code_input, full_ctx,
                                                          feedback, previous_kernel),
                    "response": kernel_code,
                    **(stats or {})})

        # LLM feedback for next round
        feedback_text = json.dumps({"profile": stats, "correct": True}) \
                        if errors == "" else errors
        feedback = KernelFeedbackAnalyser().analyse(kernel_code, feedback_text)

        previous_kernel = kernel_code
        i += 1

    # ────────────────────────── PHASE 2 : HPO ─────────────────────────────
    if best_code is None:
        log.append({"event": "no_valid_kernel", "torch": torch_file})
        return

    op_type = analyser.classify(torch_expl)
    best_code, best_us, best_stats_hpo = _run_hpo_phase2(
        initial_best_code=best_code,
        initial_best_us=best_us,
        baseline_us=baseline_us,
        op_type=op_type,
        runner=runner,
        kernel_lang=kernel_lang,
        run_dir=run_dir,
        stem=torch_path.stem
    )
    if best_stats_hpo:
        best_stats = best_stats_hpo

    # ────────────────────────── final save  ───────────────────────────────
    if best_code:
        _save_best_kernel(best_code, best_stats, run_dir,
                          torch_path.stem, "best", kernel_lang)
    else:
        log.append({"event": "no_valid_kernel", "torch": torch_file})

def orchestrate_kernel_optimization(kernel_file: str, iterations: int | None):
    """
    Same compile/runtime/HPO engine as above, but starting from an existing
    kernel (so we swap TorchAnalyser ↔ KernelAnalyser + KernelOptimizer).
    """

    kpath   = Path(kernel_file)
    run_dir = LOG_DIR / f"{kpath.stem}_opt"
    run_dir.mkdir(exist_ok=True)

    # stopping config
    min_iters       = iterations or STOP_CFG["min_iters"]
    max_iters       = iterations or STOP_CFG["max_iters"]
    patience_phase1 = STOP_CFG["patience"]
    eps             = STOP_CFG["min_improvement"]
    target_speedup  = STOP_CFG["target_speedup"]
    max_fail        = STOP_CFG["max_failures"]

    kernel_lang     = PIPELINE_CFG.get("kernel_lang", "hip").lower()
    analyser        = KernelAnalyser(kernel_lang=kernel_lang)
    optimizer       = KernelOptimizer(kernel_lang=kernel_lang)
    feedback_an     = KernelFeedbackAnalyser()
    researcher      = RAGResearcher(kernel_lang=kernel_lang)
    searcher        = SearchAgent()
    runner          = Executor(kernel_lang=kernel_lang)

    kernel_code     = kpath.read_text()
    analysis_raw    = analyser.analyse(kernel_code)
    expl, op_type, opps, bottlenecks, complex_txt = _parse_kernel_analysis(analysis_raw)

    # baseline
    try:
        base_stats, _, _ = _compile_and_profile(runner, kernel_code, kernel_lang)
        baseline_us  = _extract_latency_us(base_stats) or 1000.0
    except Exception as exc:
        print("Baseline run failed:", exc)
        base_stats   = {}
        baseline_us  = 1000.0

    best_code  = kernel_code
    best_us    = baseline_us
    best_stats = base_stats
    i = no_gain = fails = 0
    feedback    = ""
    prev_kernel = kernel_code

    # static ctx
    query      = f"{expl} optimisation {kernel_lang}"
    doc_ctx    = "\n\n[Documentation Context]\n" + researcher.query(query) \
                 if PIPELINE_CFG.get('rag_enabled') else ''
    search_ctx = "\n\n[Internet Search Results]\n" + searcher.search(query) \
                 if PIPELINE_CFG.get('online_search') else ''
    full_ctx   = doc_ctx + search_ctx

    # ─────────────── phase-1 optimisation loop ────────────────────────────
    while True:
        log.append({"event": "optimization_iteration_start", "iter": i})

        opt_ctx = (
            f"Kernel Analysis:\n{expl}\n\n"
            f"Operation Type: {op_type}\n\n"
            f"Optimization Opportunities:\n" +
            "\n".join(f"- {o}" for o in opps) + "\n\n" +
            "Current Bottlenecks:\n" +
            "\n".join(f"- {b}" for b in bottlenecks) + "\n\n" +
            f"Complexity Analysis:\n{complex_txt}\n"
        )

        optimized_code = optimizer.optimize(
            kernel_code=prev_kernel,
            analysis=opt_ctx,
            doc_context=full_ctx,
            feedback=feedback,
            iter_idx=i
        )

        try:
            stats, errors, kfile = _compile_and_profile(runner, optimized_code,
                                                        kernel_lang)
            fails = 0
        except Exception as exc:
            fails += 1
            feedback = str(exc)
            log.append({"event": "optimization_iteration_failed",
                        "iter": i, "error": feedback})
            if fails >= max_fail or i + 1 >= max_iters:
                log.append({"event": "early_stop",
                            "reason": "too_many_failures"
                                      if fails >= max_fail else "max_iters"})
                return
            i += 1
            continue

        hip_us_raw = _extract_latency_us(stats)
        hip_us     = hip_us_raw if hip_us_raw is not None else float("inf")
        speedup    = baseline_us / hip_us if hip_us != float("inf") else 0.0
        correct    = errors == ""

        # stop conditions
        reached_target  = correct and speedup >= target_speedup
        out_of_patience = correct and (no_gain >= patience_phase1) \
                          and (i + 1 >= min_iters)
        if reached_target or out_of_patience or (i + 1) >= max_iters:
            reason = ("target_speedup" if reached_target
                      else "no_improvement" if out_of_patience
                      else "max_iters")
            log.append({"event": "early_log_optimization", "iter": i,
                        "reason": reason, "speedup": speedup, "correct": correct})
            if correct:
                best_code  = optimized_code
                best_us    = hip_us
                best_stats = {"iter": i, "stats": stats,
                              "speedup": speedup, "baseline_us": baseline_us}
                _save_best_kernel(best_code, best_stats, run_dir,
                                  kpath.stem, f"iter{i}", kernel_lang)
                break

        # bookkeeping
        improved = hip_us_raw is not None and (best_us - hip_us) / best_us >= eps
        no_gain  = 0 if improved else no_gain + 1

        log.append({"event": "optimization_iteration_complete", "iter": i,
                    "correct": correct, "speedup": speedup, "hip_us": hip_us_raw,
                    "prompt": opt_ctx, "response": optimized_code,
                    **(stats or {})})

        feedback_text = json.dumps({"profile": stats, "correct": True}) \
                        if errors == "" else errors
        feedback   = feedback_an.analyse(optimized_code, feedback_text)
        prev_kernel = optimized_code
        i += 1

    # ─────────────── phase-2 HPO (shared helper) ──────────────────────────
    best_code, best_us, best_stats_hpo = _run_hpo_phase2(
        initial_best_code=best_code,
        initial_best_us=best_us,
        baseline_us=baseline_us,
        op_type=op_type,
        runner=runner,
        kernel_lang=kernel_lang,
        run_dir=run_dir,
        stem=kpath.stem
    )
    if best_stats_hpo:
        best_stats = best_stats_hpo

    _save_best_kernel(best_code, best_stats, run_dir,
                      kpath.stem, "optimized", kernel_lang)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--torch-file",  default="input_torch.py")
    ap.add_argument("--kernel-file", default=None)
    ap.add_argument("--iterations",  type=int, default=None)
    ap.add_argument("--mode", choices=["pytorch2kernel", "kernel2kernel"],
                    default=None)
    args = ap.parse_args()

    mode = args.mode or PIPELINE_CFG.get("mode", "pytorch2kernel")

    if mode == "kernel2kernel":
        if not args.kernel_file or not Path(args.kernel_file).exists():
            print("--kernel-file is required & must exist for kernel2kernel mode")
            exit(1)
        print(f"Running kernel optimisation on {args.kernel_file}")
        orchestrate_kernel_optimization(args.kernel_file, args.iterations)
    else:
        if not Path(args.torch_file).exists():
            print(f"PyTorch file {args.torch_file} does not exist")
            exit(1)
        print(f"Running PyTorch → kernel pipeline on {args.torch_file}")
        orchestrate(args.torch_file, args.iterations)
