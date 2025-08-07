from __future__ import annotations
import argparse, pathlib, yaml, json, time, shutil, os
from pathlib import Path
from typing import Dict, Any
from .torch_analyser import TorchAnalyser
from .feedback_analyzer import KernelFeedbackAnalyser
from .baseline import baseline_latency
from .rag_researcher import RAGResearcher
from .kernel_generator import KernelGenerator
from .parallel_kernel_generator import ParallelKernelGenerator
from .enhanced_parallel_generator import EnhancedParallelKernelGenerator
from .kernel_analyser import KernelAnalyser
from .kernel_optimizer import KernelOptimizer
from .executor import Executor
from ..utils.logger import log
from ..utils.kernel_compiler import compile_kernel
from ..utils.rocprof_parser import profile
from .search_agent import SearchAgent
import re
from collections import deque
from ..eval.correctness import max_abs_err
from src.optim.bayes   import BayesOpt
from src.optim.genetic import GeneticOpt
from pathlib import Path
import base64
import requests

SERVER_URL = "http://localhost:8081"

CFG = yaml.safe_load(open("config.yml"))
STOP_CFG   = CFG["stopping"]
PIPELINE_CFG    = CFG.get("Pipeline", {})
EVAL_CFG    = CFG.get("eval", {})
HIP_CFG     = CFG.get("hip",  {})
LOG_DIR   = pathlib.Path("logs")
LOG_DIR.mkdir(exist_ok=True)
SEARCH_CFG = PIPELINE_CFG['search']
ATOL     = float(EVAL_CFG.get("atol", 1e-3))
CHK_NUM    = PIPELINE_CFG.get("enable_correctness", False) 
CHK_Help   = PIPELINE_CFG.get("help_injection", False) 

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

def _parse_torch_analysis(torch_expl_raw, kernel_lang):
    # Decode the JSON response
    try:
        # Extract JSON from the response if it's wrapped in ```json blocks
        json_match = re.search(r'```json\n(.*?)\n```', torch_expl_raw, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            json_str = torch_expl_raw
        
        torch_analysis = json.loads(json_str)
        torch_expl = torch_analysis.get("explanation", "")
        kernels = torch_analysis.get("top_kernels", [])
        
    except (json.JSONDecodeError, AttributeError) as e:
        print(f"Failed to decode torch analysis JSON: {e}")
        torch_expl = torch_expl_raw
        kernels = []

    cheat_code = ""
    path_sheets = Path(__file__).parent.parent / "sheets"
    if os.path.exists(path_sheets):
        if len(kernels) > 0:
            kernel_sheet = os.path.join(path_sheets, kernel_lang + ".json")
            with open(kernel_sheet, 'r') as f:
                kernel_data = json.load(f)
            # loop over all kernels and make one string with join \n pulling "name" and "kernel"
            cheat_code = "\n   "
            
            for kernel_name in kernels:
                # Find the kernel in the list of kernel dictionaries
                for kernel_entry in kernel_data['kernels']:
                    if kernel_entry.get('name') == kernel_name:
                        # Format the string with name and kernel
                        cheat_code += f"{kernel_entry['name']}: the torch code: \n\n {kernel_entry['pytorch']} \n\n and corresponding kernel code: \n\n {kernel_entry['kernel']}\n"
                        break

    return torch_expl, cheat_code

def _parse_kernel_analysis(analysis_raw):
    """Parse kernel analysis JSON response."""
    try:
        # Extract JSON from the response if it's wrapped in ```json blocks
        json_match = re.search(r'```json\n(.*?)\n```', analysis_raw, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            json_str = analysis_raw
        
        analysis = json.loads(json_str)
        explanation = analysis.get("explanation", "")
        op_type = analysis.get("operation_type", "other")
        opportunities = analysis.get("optimization_opportunities", [])
        bottlenecks = analysis.get("current_bottlenecks", [])
        complexity = analysis.get("complexity_analysis", "")
        
        return explanation, op_type, opportunities, bottlenecks, complexity
        
    except (json.JSONDecodeError, AttributeError) as e:
        print(f"Failed to decode kernel analysis JSON: {e}")
        return analysis_raw, "other", [], [], ""

def orchestrate(torch_file: str, iterations: int | None):

    torch_path = Path(torch_file)
    run_dir    = LOG_DIR / torch_path.stem
    run_dir.mkdir(exist_ok=True)

    # ---- stopping config ---------------------------------------------------
    min_iters      = STOP_CFG["min_iters"]
    max_iters      = iterations if iterations is not None else STOP_CFG["max_iters"]
    min_iters      = iterations if iterations is not None else STOP_CFG["min_iters"]
    target_speedup = STOP_CFG["target_speedup"]
    eps            = STOP_CFG["min_improvement"]
    patience_phase1= STOP_CFG["patience"]         # for LLM phase
    max_fail       = STOP_CFG["max_failures"]

    # ---- helpers -----------------------------------------------------------
    torch_code = torch_path.read_text()
    torch_name = torch_path.stem
    # Get kernel language from config
    kernel_lang = PIPELINE_CFG.get("kernel_lang", "hip").lower()

    if CHK_Help:
        path_for_help = os.path.join(Path(__file__).parent.parent.parent,'docs',kernel_lang,'kernel_bench.json')
        if os.path.exists(path_for_help):
            with open(path_for_help, 'r') as help_file:
                help_data = json.load(help_file)
                print("Help data loaded successfully.")
    
    analyser   = TorchAnalyser()
    feedback_analyzer = KernelFeedbackAnalyser() 
    researcher = RAGResearcher(kernel_lang=kernel_lang) if PIPELINE_CFG['rag_enabled'] else None  # Language-specific RAG
    searcher   = SearchAgent()
    
    # Use parallel generator if enabled, fallback to single generator
    parallel_enabled = PIPELINE_CFG.get("parallel_inference", {}).get("enabled", False)
    enhanced_mode = PIPELINE_CFG.get("parallel_inference", {}).get("enhanced_mode", False)
    
    if parallel_enabled:
        if enhanced_mode:
            generator = EnhancedParallelKernelGenerator(kernel_lang=kernel_lang)  # Enhanced parallel generator
            print("Using Enhanced Parallel Kernel Generator with Alpha Evolve for complex kernels")
        else:
            generator = ParallelKernelGenerator(kernel_lang=kernel_lang)  # Standard parallel generator
            print("Using Standard Parallel Kernel Generator for inference scaling")
    else:
        generator = KernelGenerator(kernel_lang=kernel_lang)  # Single generator
        print("Using Single Kernel Generator")
    
    runner     = Executor(kernel_lang=kernel_lang)  # Language-specific executor
    
    torch_expl_raw = analyser.analyse(torch_code)

    # ---- get explanation and corresponding kernels as cheat sheet ---------------------------------------------------
    torch_expl, cheat_code = _parse_torch_analysis(torch_expl_raw, kernel_lang)

    # ---- initialization ---------------------------------------------------
    baseline_us = baseline_latency(torch_file, n_trial=2)
    
    best_code: str | None   = None
    best_us                 = float("inf")
    best_stats: Dict[str,Any]|None = None

    i, no_gain, fails = 0, 0, 0
    feedback = ""
    previous_kernel = ""  # Track previous kernel for iterations 2+

    # ---------- build context -------------------------------------------
    doc_ctx    = "\n\n[Documentation Context]\n" + researcher.query(torch_expl) if PIPELINE_CFG['rag_enabled'] else ''
    search_ctx = "\n\n[Internet Search Results]\n" + searcher.search(torch_expl) if PIPELINE_CFG['online_search'] else ''
    full_ctx = doc_ctx + search_ctx

    # ---- Phase 1 LLM based kernel generation loop ---------------------------------------------------
    print(f"Starting Phase 1 LLM kernel generation loop:")
    print(f"  min_iters: {min_iters}, max_iters: {max_iters}")
    print(f"  target_speedup: {target_speedup}, patience: {patience_phase1}")
    print(f"  parallel_enabled: {parallel_enabled}")
    
    while True:
        print(f"\n--- Starting iteration {i} ---")
        log.append({"event": "iteration_start", "iter": i})
        code_input = torch_expl + "\n\n [Here is the PyTorch Code:] \n\n" + torch_code

        if CHK_Help:
            if i==1:
                # Inject help data into the code input if help injection is enabled
                code_input += "\n\n [Here is the help data:] \n\n" + json.dumps(help_data[torch_name]['source'], indent=2)
            
        # cheat sheet of relevant kernels is only necessary on first iteration because subsequent iterations
        # already have an existing kernel to work from that the LLM wrote
        code_input += "\n\n [Here are the available kernels to learn from:] \n\n" + cheat_code if PIPELINE_CFG['cheat_sheet'] and i == 0 else ''
        
        # Generate kernel with enhanced parallel generator if it's the enhanced version
        if isinstance(generator, EnhancedParallelKernelGenerator):
            kernel_code = generator.generate(
                torch_expl=code_input, 
                doc_context=full_ctx, 
                feedback=feedback, 
                iter_idx=i, 
                previous_kernel=previous_kernel,
                torch_file=torch_file,
                baseline_us=baseline_us
            )
        elif parallel_enabled and hasattr(generator, 'generate_parallel'):
            kernel_code = generator.generate_parallel(
                torch_expl=code_input, 
                doc_context=full_ctx, 
                feedback=feedback, 
                iter_idx=i, 
                previous_kernel=previous_kernel,
                torch_file=torch_file,
                baseline_us=baseline_us
            )
        else:
            kernel_code = generator.generate(code_input, full_ctx, feedback=feedback, iter_idx=i, previous_kernel=previous_kernel)
        
        # ---------- compile & run -------------------------------------------
        try:
            stats, errors, kernel_file = runner.run(kernel_code)
            print('-.'*70)
            print(f"                                                               errors ")
            print(f"{errors}")
            print('-.'*70)
  
            if CHK_NUM and not errors:
                # Construct shared library path from kernel file path
                kernel_dir = os.path.dirname(kernel_file)
                so_path = os.path.join(kernel_dir, "kernel.so")
                if os.path.exists(so_path):
                    try:
                        err = max_abs_err(torch_file, so_path)
                        errors = "" if err <= ATOL else f"MAX_ABS_ERR={err:.4e} > {ATOL}"
                        log.append({"event": "correctness_check", "status": "passed" if not errors else "failed", "error": errors, "actual_error": err})
                    except AttributeError as e:
                        if "undefined symbol: run_kernel" in str(e):
                            errors = "Kernel does not expose run_kernel interface for correctness checking"
                        else:
                            errors = f"Correctness check failed: {str(e)}"
                        log.append({"event": "correctness_check", "status": "failed", "error": errors})
                    except Exception as e:
                        errors = f"Correctness check error: {str(e)}"
                        log.append({"event": "correctness_check", "status": "failed", "error": errors})
                else:
                    errors = f"Shared library not found at {so_path}"
                    log.append({"event": "correctness_check", "status": "failed", "error": errors})
                correctness_triggered = True
            fails = 0
        except Exception as exc:
            fails += 1
            feedback = str(exc)
            log.append({"event": "iteration_failed", "iter": i, "error": feedback})
            # Increment iteration counter for failed iterations
            i += 1
            if fails >= max_fail or i >= max_iters:
                log.append({"event": "early_stop",
                            "reason": "too_many_failures" if fails >= max_fail else "max_iters"})
                return
            continue

        if errors is None:
            errors = ""
            if PIPELINE_CFG['omnivise']:
                # ---------- send to Omniwise for profiling ------------------------
                encoded_code = base64.b64encode(kernel_code.encode("utf-8")).decode("utf-8")
                JSON_PAYLOAD = {"architecture": CFG[kernel_lang]['gpu_arch'], "compiler_flags": "-O3", "code": encoded_code}  # original omniwise was gfx90a
                json_payload_str = json.dumps(JSON_PAYLOAD)
                response = requests.post(SERVER_URL, headers={"Content-Type": "application/json"}, data=json_payload_str)

                if response.status_code == 200:
                    omnivise_json =  json.loads(response.text)
                    stats.update(omnivise_json['data'])
        # ---------- metrics -------------------------------------------------
        hip_us_raw = _extract_latency_us(stats)
        hip_us     = hip_us_raw if hip_us_raw is not None else float("inf")
        speedup    = baseline_us / hip_us if hip_us != float("inf") else 0.0
        correct    = errors is None or errors == "" 
        # ---------- phase-1 stop conditions ---------------------------------
        reached_target  = correct and (speedup >= target_speedup)
        out_of_patience = correct and (no_gain   >= patience_phase1) and (i >= min_iters)
        # stop on max iters *regardless* of correctness
        hit_max_iters   = i >= max_iters
        hit_min_iters   = i >= min_iters

        print(f"Iteration {i} stopping conditions:")
        print(f"  correct: {correct}, speedup: {speedup:.2f}, no_gain: {no_gain}")
        print(f"  reached_target: {reached_target}, out_of_patience: {out_of_patience}")
        print(f"  hit_max_iters: {hit_max_iters}, hit_min_iters: {hit_min_iters}")

        # Modified stopping logic: only stop when correctness is achieved OR max iterations reached
        # For pytorch2kernel mode with parallel generators, prioritize correctness
        should_stop = False
        if parallel_enabled:
            # For parallel generators: continue until correct OR max iterations
            should_stop = (correct and (reached_target or out_of_patience or hit_min_iters)) or hit_max_iters
        else:
            # Original logic for single generator
            should_stop = reached_target or out_of_patience or hit_max_iters
        
        print(f"  should_stop: {should_stop}")

        if should_stop:
            print(f"\n=== STOPPING PHASE 1 after iteration {i} ===")
            print(f"Stopping reason analysis:")
            print(f"  correct: {correct}")
            print(f"  reached_target: {reached_target}")  
            print(f"  out_of_patience: {out_of_patience}")
            print(f"  hit_max_iters: {hit_max_iters}")
            print(f"  hit_min_iters: {hit_min_iters}")
            print(f"  parallel_enabled: {parallel_enabled}")
            
            if parallel_enabled:
                # For parallel generators: determine reason based on new logic
                if correct and reached_target:
                    reason = "target_speedup"
                elif correct and out_of_patience:
                    reason = "no_improvement"
                elif correct and hit_min_iters:
                    reason = "correctness_achieved"
                elif hit_max_iters:
                    reason = "max_iters"
                else:
                    reason = "unknown"
            else:
                # Original reason logic for single generator
                reason = (
                    "target_speedup"    if reached_target
                    else "no_improvement" if out_of_patience
                    else "max_iters"
                )
            
            print(f"  final_reason: {reason}")
            print("=" * 50)
            log.append({
                "event" : "early_log",
                "reason": reason,
                "iter"  : i,
                "speedup": speedup,
                "correct": correct
            })

            # ---------- keep a correct kernel? ----------------------------------
            if correct:
                best_code  = Path(kernel_file).read_text()
                best_us    = hip_us
                best_stats = {"iter": i, "stats": stats,
                            "speedup": speedup, "baseline_us": baseline_us}
                
                # Get kernel language from config to set proper file extension
                kernel_lang = PIPELINE_CFG.get("kernel_lang", "hip").lower()
                extension_map = {"hip": ".hip", "cuda": ".cu", "triton": ".py"}
                ext = extension_map.get(kernel_lang, ".hip")
                
                shutil.copy(kernel_file, run_dir / f"{torch_path.stem}_iter{i}{ext}")
                if hit_min_iters:
                    log.append({                         # SFT sample (phase-1)
                        "event"   : "sft_sample_phase1",
                        "prompt"  : torch_code,
                        "response": kernel_code,
                        "iter"    : i,
                        "speedup" : speedup,
                        "hip_us"  : hip_us_raw,
                    })
                    log.append({"event":"phase1_complete","iter":i,"hip_us":hip_us})
            
            # Always break when stopping condition is met, regardless of correctness
            print(f"Breaking from Phase 1 loop due to: {reason}")
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
            "prompt"   : generator.build_user_prompt(code_input, full_ctx, feedback, previous_kernel),
            "response" : kernel_code,
            **(stats or {})
        })

        # Increment iteration counter AFTER completion of this iteration
        i += 1
        print(f"--- Completed PyTorch->Kernel iteration {i-1}, starting next iteration {i} ---")

        if not errors or errors == "":
            feedback_text = json.dumps({"profile": stats,"correct": True})
        else:
            feedback_text = errors

        feedback = feedback_analyzer.analyse(kernel_code, feedback_text) 
        print('&'*70)
        print(feedback)
        print('&'*70)
        # Store current kernel as previous for next iteration  
        previous_kernel = kernel_code
        
        # ---- GEAK-Agent inspired debugging trap prevention ----------------
        # Check if we're stuck in a debugging loop with the same error
        if feedback:
            current_error_signature = feedback[:200]  # Use first 200 chars as error signature
            if not hasattr(feedback_analyzer, '_error_history'):
                feedback_analyzer._error_history = []
            
            # Check for repeated errors (debugging trap detection)
            similar_errors = sum(1 for prev_error in feedback_analyzer._error_history 
                               if prev_error == current_error_signature)
            
            feedback_analyzer._error_history.append(current_error_signature)
            
            # If we've seen this error 3+ times, suggest alternative approach
            if similar_errors >= 2:
                log.append({
                    "event": "debugging_trap_detected",
                    "iter": i-1,  # Use i-1 since we already incremented
                    "repeated_error": current_error_signature,
                    "count": similar_errors + 1
                })
                
                # Add alternative strategy suggestion to feedback
                alternative_strategy = """
                
                DEBUGGING TRAP DETECTED: This error has occurred multiple times.
                Consider alternative approaches:
                1. Simplify the algorithm - use a more basic implementation
                2. Change the data layout or memory access patterns
                3. Use different mathematical formulations
                4. Break down complex operations into simpler steps
                5. Try a completely different algorithmic approach
                
                Focus on finding a working solution rather than optimizing the current approach.
                """
                feedback += alternative_strategy

    # ----------------  no correct kernel → abort whole run ------------------
    if best_code is None:
        log.append({"event": "no_valid_kernel", "torch": torch_file})
        return

    # ============================  PHASE 2 – HPO  ===============================
    #  optimiser selection
    op_type = analyser.classify(torch_expl)
    if op_type == "elem":
        search_space = {"block_size":[64,128,256,512,32,1024],}
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

    
    # ---- Phase 2 GO/BO loop ---------------------------------------------------
    no_gain_hpo = 0
    if SEARCH_CFG['enabled']:
        counter=0
        while True:
            counter+=1
            print(f"Phase-2 HPO iteration {counter} for optimiser {optimiser.__class__.__name__}")
            sample = optimiser.next_params()
            if sample is None:
                log.append({"event":"early_stop_hpo","reason":"budget_exhausted"})
                break

            tunables = {k: v for k, v in sample.items() if not k.startswith("_")}
            patched_code = _apply_tunables(best_code, tunables)

            try:
                stats, errs_ , kernel_file = runner.run(patched_code)
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
            out_of_patience_hpo = no_gain_hpo >= SEARCH_CFG.get("patience", 16)
            if out_of_patience_hpo:
                reason = ("target_speedup" if reached_target_hpo else "no_improvement")
                log.append({"event":"early_stop_hpo","reason":reason,"speedup":speedup})
                break

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
        if kernel_lang in ["hip", "cuda"]:
            shutil.copy(best_path, run_dir / f"{torch_path.stem}_best.cpp")
        with best_path.with_suffix(".json").open("w") as fp:
            json.dump(best_stats, fp, indent=2)
        log.append({"event":"best_kernel_saved","file":str(best_path),**best_stats})
    else:
        log.append({"event":"no_valid_kernel","torch":torch_file})

def orchestrate_kernel_optimization(kernel_file: str, iterations: int | None):
    """
    Orchestrate kernel-to-kernel optimization pipeline.
    """
    kernel_path = Path(kernel_file)
    run_dir = LOG_DIR / f"{kernel_path.stem}_opt"
    run_dir.mkdir(exist_ok=True)

    # ---- stopping config ---------------------------------------------------
    min_iters = STOP_CFG["min_iters"]
    max_iters = iterations if iterations is not None else STOP_CFG["max_iters"]
    min_iters = iterations if iterations is not None else STOP_CFG["min_iters"]
    target_speedup = STOP_CFG["target_speedup"]
    eps = STOP_CFG["min_improvement"]
    patience_phase1 = STOP_CFG["patience"]
    max_fail = STOP_CFG["max_failures"]

    # ---- helpers -----------------------------------------------------------
    kernel_code = kernel_path.read_text()

    # Get kernel language from config
    kernel_lang = PIPELINE_CFG.get("kernel_lang", "hip").lower()
    
    analyser = KernelAnalyser(kernel_lang=kernel_lang)
    feedback_analyzer = KernelFeedbackAnalyser()
    researcher = RAGResearcher(kernel_lang=kernel_lang) if PIPELINE_CFG['rag_enabled'] else None
    searcher = SearchAgent()
    
    # Use parallel generator if enabled, fallback to single optimizer
    parallel_enabled = PIPELINE_CFG.get("parallel_inference", {}).get("enabled", False)
    enhanced_mode = PIPELINE_CFG.get("parallel_inference", {}).get("enhanced_mode", False)
    
    if parallel_enabled:
        if enhanced_mode:
            optimizer = EnhancedParallelKernelGenerator(kernel_lang=kernel_lang)  # Enhanced parallel optimizer
            print("Using Enhanced Parallel Kernel Generator for kernel2kernel optimization")
        else:
            optimizer = ParallelKernelGenerator(kernel_lang=kernel_lang)  # Standard parallel optimizer
            print("Using Standard Parallel Kernel Generator for kernel2kernel optimization")
    else:
        optimizer = KernelOptimizer(kernel_lang=kernel_lang)  # Single optimizer
        print("Using Single Kernel Optimizer for kernel2kernel mode")
    
    runner = Executor(kernel_lang=kernel_lang)

    # ---- analyze the input kernel ------------------------------------------
    analysis_raw = analyser.analyse(kernel_code)
    explanation, op_type, opportunities, bottlenecks, complexity = _parse_kernel_analysis(analysis_raw)
    
    print(f"Kernel Analysis:")
    print(f"  Operation Type: {op_type}")
    print(f"  Explanation: {explanation}")
    print(f"  Optimization Opportunities: {opportunities}")
    print(f"  Current Bottlenecks: {bottlenecks}")
  
    # ---- get baseline performance ------------------------------------------
    try:
        baseline_stats, baseline_errors, _ = runner.run(kernel_code)
        baseline_us = _extract_latency_us(baseline_stats)
        if baseline_us is None:
            print("Warning: Could not get baseline performance, using dummy value")
            baseline_us = 1000.0  # dummy baseline
    except Exception as exc:
        print(f"Error getting baseline performance: {exc}")
        baseline_us = 1000.0  # dummy baseline

    # ---- initialization ---------------------------------------------------
    best_code: str | None = kernel_code  # Start with input kernel as best
    best_us = baseline_us
    best_stats: Dict[str, Any] | None = baseline_stats

    i, no_gain, fails = 0, 0, 0
    feedback = ""
    previous_kernel = kernel_code

    # ---------- build context -------------------------------------------
    search_query = f"{explanation} optimization {kernel_lang}"
    doc_ctx = "\n\n[Documentation Context]\n" + researcher.query(search_query) if PIPELINE_CFG['rag_enabled'] else ''
    search_ctx = "\n\n[Internet Search Results]\n" + searcher.search(search_query) if PIPELINE_CFG['online_search'] else ''
    full_ctx = doc_ctx + search_ctx

    # ---- Phase 1 LLM based kernel optimization loop ---------------------------------------------------
    while True:
        log.append({"event": "optimization_iteration_start", "iter": i})
        
        # Create optimization context
        optimization_context = f"""
                                Kernel Analysis:
                                {explanation}

                                Operation Type: {op_type}

                                Optimization Opportunities:
                                {chr(10).join(f"- {opp}" for opp in opportunities)}

                                Current Bottlenecks:
                                {chr(10).join(f"- {bottleneck}" for bottleneck in bottlenecks)}

                                Complexity Analysis:
                                {complexity}
                                """

        # Generate optimized kernel using parallel generators or single optimizer
        if parallel_enabled and isinstance(optimizer, (ParallelKernelGenerator, EnhancedParallelKernelGenerator)):
            # For parallel generators, use the generate method with optimization context as torch_expl
            optimized_code = optimizer.generate(
                torch_expl=optimization_context,
                doc_context=full_ctx,
                feedback=feedback,
                iter_idx=i,
                previous_kernel=previous_kernel,
                torch_file="",  # No torch file for kernel2kernel mode
                baseline_us=baseline_us
            )
        else:
            # For single optimizer, use the optimize method
            optimized_code = optimizer.optimize(
                kernel_code=previous_kernel,
                analysis=optimization_context,
                doc_context=full_ctx,
                feedback=feedback,
                iter_idx=i
            )

        # ---------- compile & run -------------------------------------------
        try:
            stats, errors, kernel_file_path = runner.run(optimized_code)
            print('-.' * 70)
            print(f"                                                               errors ")
            print(f"{errors}")
            print('-.' * 70)
            fails = 0
        except Exception as exc:
            fails += 1
            feedback = str(exc)
            log.append({"event": "optimization_iteration_failed", "iter": i, "error": feedback})
            # Increment iteration counter for failed iterations
            i += 1
            if fails >= max_fail or i >= max_iters:
                log.append({"event": "early_stop",
                            "reason": "too_many_failures" if fails >= max_fail else "max_iters"})
                return
            continue

        if errors is None:
            errors = ""
            if PIPELINE_CFG['omnivise']:
                # ---------- send to Omniwise for profiling ------------------------
                encoded_code = base64.b64encode(optimized_code.encode("utf-8")).decode("utf-8")
                JSON_PAYLOAD = {"architecture": CFG[kernel_lang]['gpu_arch'], "compiler_flags": "-O3", "code": encoded_code}
                json_payload_str = json.dumps(JSON_PAYLOAD)
                response = requests.post(SERVER_URL, headers={"Content-Type": "application/json"}, data=json_payload_str)

                if response.status_code == 200:
                    omnivise_json = json.loads(response.text)
                    stats.update(omnivise_json['data'])

        # ---------- metrics -------------------------------------------------
        hip_us_raw = _extract_latency_us(stats)
        hip_us = hip_us_raw if hip_us_raw is not None else float("inf")
        speedup = baseline_us / hip_us if hip_us != float("inf") else 0.0
        correct = errors is None or errors == ""

        # ---------- phase-1 stop conditions ---------------------------------
        reached_target = correct and (speedup >= target_speedup)
        out_of_patience = correct and (no_gain >= patience_phase1) and (i >= min_iters)
        hit_max_iters = i >= max_iters
        hit_min_iters = i >= min_iters

        # Modified stopping logic for kernel2kernel mode: prioritize correctness when using parallel generators
        should_stop = False
        if parallel_enabled:
            # For parallel generators in kernel2kernel mode: continue until correct OR max iterations
            should_stop = (correct and (reached_target or out_of_patience or hit_min_iters)) or hit_max_iters
        else:
            # Original logic for single optimizer
            should_stop = reached_target or out_of_patience or hit_max_iters

        if should_stop:
            if parallel_enabled:
                # For parallel generators: determine reason based on new logic
                if correct and reached_target:
                    reason = "target_speedup"
                elif correct and out_of_patience:
                    reason = "no_improvement"
                elif correct and hit_min_iters:
                    reason = "correctness_achieved"
                elif hit_max_iters:
                    reason = "max_iters"
                else:
                    reason = "unknown"
            else:
                # Original reason logic for single optimizer
                reason = (
                    "target_speedup" if reached_target
                    else "no_improvement" if out_of_patience
                    else "max_iters"
                )
            log.append({
                "event": "early_log_optimization",
                "reason": reason,
                "iter": i,
                "speedup": speedup,
                "correct": correct
            })
            # ---------- keep a correct kernel? ----------------------------------
            if correct:
                best_code = optimized_code
                best_us = hip_us
                best_stats = {"iter": i, "stats": stats,
                              "speedup": speedup, "baseline_us": baseline_us}

                # Get proper file extension for current kernel language
                extension_map = {"hip": ".hip", "cuda": ".cu", "triton": ".py"}
                ext = extension_map.get(kernel_lang, ".hip")

                shutil.copy(kernel_file_path, run_dir / f"{kernel_path.stem}_opt_iter{i}{ext}")
                if hit_min_iters:
                    log.append({
                        "event": "sft_sample_optimization_phase1",
                        "original_kernel": kernel_code,
                        "optimized_kernel": optimized_code,
                        "iter": i,
                        "speedup": speedup,
                        "hip_us": hip_us_raw,
                    })
                    log.append({"event": "optimization_phase1_complete", "iter": i, "hip_us": hip_us})
            
            # Always break when stopping condition is met, regardless of correctness
            print(f"Breaking from Kernel Optimization loop due to: {reason}")
            break

        # bookkeeping for 'no‐gain'
        if hip_us_raw is not None:
            improved = (best_us - hip_us) / best_us >= eps
            no_gain = 0 if improved else no_gain + 1
        else:
            no_gain += 1

        log.append({
            "event": "optimization_iteration_complete",
            "iter": i,
            "correct": correct,
            "speedup": speedup,
            "hip_us": hip_us_raw,
            "prompt": optimization_context,
            "response": optimized_code,
            **(stats or {})
        })

        # Increment iteration counter AFTER completion of this iteration
        i += 1
        print(f"--- Completed Kernel->Kernel optimization iteration {i-1}, starting next iteration {i} ---")

        if not errors or errors == "":
            feedback_text = json.dumps({"profile": stats, "correct": True})
        else:
            feedback_text = errors

        feedback = feedback_analyzer.analyse(optimized_code, feedback_text)
        print('&' * 70)
        print(feedback)
        print('&' * 70)
        
        # ---- GEAK-Agent inspired debugging trap prevention ----------------
        # Check if we're stuck in a debugging loop with the same error
        if feedback:
            current_error_signature = feedback[:200]  # Use first 200 chars as error signature
            if not hasattr(feedback_analyzer, '_error_history_kernel_opt'):
                feedback_analyzer._error_history_kernel_opt = []
            
            # Check for repeated errors (debugging trap detection)
            similar_errors = sum(1 for prev_error in feedback_analyzer._error_history_kernel_opt 
                               if prev_error == current_error_signature)
            
            feedback_analyzer._error_history_kernel_opt.append(current_error_signature)
            
            # If we've seen this error 3+ times, suggest alternative approach
            if similar_errors >= 2:
                log.append({
                    "event": "debugging_trap_detected_kernel_opt",
                    "iter": i-1,  # Use i-1 since we already incremented
                    "repeated_error": current_error_signature,
                    "count": similar_errors + 1
                })
                
                # Add alternative strategy suggestion to feedback
                alternative_strategy = """
                
                DEBUGGING TRAP DETECTED: This error has occurred multiple times.
                Consider alternative approaches:
                1. Simplify the optimization - use more conservative changes
                2. Change the memory access patterns or data layout
                3. Use different optimization techniques (e.g., loop unrolling vs vectorization)
                4. Break down complex optimizations into smaller incremental steps
                5. Try a completely different optimization strategy
                6. Revert to a simpler working version and optimize differently
                
                Focus on finding a working optimized solution rather than perfecting the current approach.
                """
                feedback += alternative_strategy
        
        # Store current kernel as previous for next iteration
        previous_kernel = optimized_code

    # ----------------  no valid optimized kernel → return original ------------------
    if best_code is None:
        log.append({"event": "no_valid_optimized_kernel", "kernel": kernel_file})
        best_code = kernel_code
        best_us = baseline_us

    # ============================  PHASE 2 – HPO  ===============================
    # Use the same HPO logic as the original orchestrate function
    if op_type == "elem":
        search_space = {"block_size": [64, 128, 256, 512, 32, 1024], }
    elif op_type == "reduce":
        search_space = {"block_size": [64, 128, 256],
                        "vector_width": [1, 2, 4]}
    else:  # gemm/conv
        search_space = {"block_size": [128, 256, 512],
                        "tile_m": [8, 16, 32, 64],
                        "tile_n": [8, 16, 32, 64]}

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

    # ---- Phase 2 GO/BO loop (same as original) ---------------------------------------------------
    no_gain_hpo = 0
    if SEARCH_CFG['enabled']:
        counter = 0
        while True:
            counter += 1
            print(f"Phase-2 HPO iteration {counter} for optimiser {optimiser.__class__.__name__}")
            sample = optimiser.next_params()
            if sample is None:
                log.append({"event": "early_stop_hpo", "reason": "budget_exhausted"})
                break

            tunables = {k: v for k, v in sample.items() if not k.startswith("_")}
            patched_code = _apply_tunables(best_code, tunables)

            try:
                stats, errs_, kernel_file_path = runner.run(patched_code)
            except Exception as exc:
                log.append({"event": "hpo_compile_fail",
                            "params": tunables, "err": str(exc)})
                optimiser.update(sample.get("_trial") or sample.get("_ind"), 0.0)
                continue

            hip_us_raw = _extract_latency_us(stats)
            hip_us = hip_us_raw if hip_us_raw is not None else float("inf")
            speedup = baseline_us / hip_us if hip_us != float("inf") else 0.0

            optimiser.update(sample.get("_trial") or sample.get("_ind"), speedup)
            log.append({"event": "hpo_step", "params": tunables,
                        "hip_us": hip_us_raw, "speedup": speedup})
            improved = hip_us < best_us and hip_us_raw is not None
            if improved:
                best_us = hip_us
                best_code = patched_code
                best_stats = {"params": tunables, "stats": stats,
                              "speedup": speedup, "baseline_us": baseline_us}

                # Get proper file extension for current kernel language
                extension_map = {"hip": ".hip", "cuda": ".cu", "triton": ".py"}
                ext = extension_map.get(kernel_lang, ".hip")

                shutil.copy(kernel_file_path, run_dir / f"{kernel_path.stem}_opt_HPO_{int(speedup * 100):03}{ext}")

                log.append({
                    "event": "sft_sample_optimization_hpo",
                    "original_kernel": kernel_code,
                    "optimized_kernel": patched_code,
                    "params": tunables,
                    "speedup": speedup,
                    "hip_us": hip_us_raw,
                })
                no_gain_hpo = 0
            else:
                no_gain_hpo += 1

            # ---- Phase-2 stop conditions --------------------------------------
            reached_target_hpo = speedup >= target_speedup
            out_of_patience_hpo = no_gain_hpo >= SEARCH_CFG.get("patience", 16)
            if out_of_patience_hpo:
                reason = ("target_speedup" if reached_target_hpo else "no_improvement")
                log.append({"event": "early_stop_hpo", "reason": reason, "speedup": speedup})
                break

    # =========================================================================
    #  final persistence
    # =========================================================================
    if best_code:
        # Get proper file extension for current kernel language
        extension_map = {"hip": ".hip", "cuda": ".cu", "triton": ".py"}
        ext = extension_map.get(kernel_lang, ".hip")

        best_path = run_dir / f"{kernel_path.stem}_optimized{ext}"
        best_path.write_text(best_code)
        if kernel_lang in ["hip", "cuda"]:
            shutil.copy(best_path, run_dir / f"{kernel_path.stem}_optimized.cpp")
        with best_path.with_suffix(".json").open("w") as fp:
            json.dump(best_stats, fp, indent=2)
        log.append({"event": "best_optimized_kernel_saved", "file": str(best_path), **best_stats})
    else:
        log.append({"event": "no_valid_optimized_kernel", "kernel": kernel_file})

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--torch-file", default="input_torch.py", dest="torch_file",
                    help="PyTorch file for pytorch2kernel mode")
    ap.add_argument("--kernel-file", default=None, dest="kernel_file",
                    help="Kernel file for kernel2kernel optimization mode")
    ap.add_argument("--iterations", type=int, default=None,
                    help="force fixed iteration count (skip auto-stop)")
    ap.add_argument("--mode", choices=["pytorch2kernel", "kernel2kernel"], default=None,
                    help="Pipeline mode (overrides config)")
    args = ap.parse_args()
    
    # Determine mode
    mode = args.mode or PIPELINE_CFG.get("mode", "pytorch2kernel")

    if mode == "kernel2kernel":
        if not args.kernel_file:
            print("Error: --kernel-file is required for kernel2kernel mode")
            exit(1)
        if not Path(args.kernel_file).exists():
            print(f"Error: Kernel file {args.kernel_file} does not exist")
            exit(1)
        print(f"Running kernel optimization pipeline on {args.kernel_file}")
        orchestrate_kernel_optimization(args.kernel_file, args.iterations)
    else:  # pytorch2kernel
        if not Path(args.torch_file).exists():
            print(f"Error: PyTorch file {args.torch_file} does not exist")
            exit(1)
        print(f"Running PyTorch to kernel pipeline on {args.torch_file}")
        orchestrate(args.torch_file, args.iterations)
