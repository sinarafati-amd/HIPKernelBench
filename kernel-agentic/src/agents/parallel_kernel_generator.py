"""
Parallel Kernel Generator for inference time scaling.
Generates multiple kernel candidates in parallel using different models and selects the best one.
"""

import asyncio
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Tuple, Optional
from pathlib import Path
import time
import yaml
import json
import threading
from dataclasses import dataclass

from .kernel_generator import KernelGenerator
from .executor import Executor
from ..eval.correctness import max_abs_err
from ..utils.logger import log
from ..utils.kernel_compiler import compile_kernel
from ..utils.rocprof_parser import profile

CFG = yaml.safe_load(open("config.yml"))
PIPELINE_CFG = CFG.get("Pipeline", {})
PARALLEL_CFG = PIPELINE_CFG.get("parallel_inference", {})
EVAL_CFG = CFG.get("eval", {})
ATOL = float(EVAL_CFG.get("atol", 1e-3))
CHK_NUM = PIPELINE_CFG.get("enable_correctness", False)

@dataclass
class KernelCandidate:
    """Represents a generated kernel candidate with its metadata"""
    code: str
    agent_name: str
    instance_id: int
    generation_time: float
    compile_success: bool = False
    runtime_success: bool = False
    correctness_pass: bool = False
    latency_us: Optional[float] = None
    speedup: Optional[float] = None
    errors: str = ""
    stats: Optional[Dict[str, Any]] = None
    score: float = 0.0  # Combined score for ranking

    def __post_init__(self):
        """Calculate combined score after initialization"""
        self.calculate_score()
    
    def calculate_score(self):
        """Calculate a combined score for ranking candidates"""
        # Base score starts at 0
        score = 0.0
        
        # Compilation success: +100 points
        if self.compile_success:
            score += 100
            
        # Runtime success: +50 points  
        if self.runtime_success:
            score += 50
            
        # Correctness: +200 points (most important)
        if self.correctness_pass:
            score += 200
            
        # Performance bonus: up to +150 points based on speedup
        if self.speedup and self.speedup > 0:
            # Logarithmic scaling for speedup
            import math
            score += min(150, 50 * math.log(self.speedup + 1))
            
        # Penalty for latency (lower is better): up to -50 points
        if self.latency_us and self.latency_us > 0:
            # Normalize latency penalty (assuming reasonable range 1-10000 us)
            normalized_latency = min(1.0, self.latency_us / 10000.0)
            score -= 50 * normalized_latency
            
        # Generation time bonus (faster is better): up to +20 points
        if self.generation_time > 0:
            # Bonus for fast generation (under 30 seconds gets full bonus)
            time_bonus = max(0, 20 * (1 - min(1.0, self.generation_time / 30.0)))
            score += time_bonus
            
        self.score = score

def _extract_latency_us(stats: Dict[str, Any] | None) -> float | None:
    """Extract average latency from stats dict"""
    if not stats:
        return None
    val = stats.get("avg_us")
    try:
        return float(val) if val and val > 0 else None
    except (TypeError, ValueError):
        return None

class ParallelKernelGenerator:
    """
    Manages parallel kernel generation using multiple models and instances.
    """
    
    def __init__(self, kernel_lang: str = None):
        if kernel_lang is None:
            kernel_lang = PIPELINE_CFG.get("kernel_lang", "hip").lower()
        
        self.kernel_lang = kernel_lang
        self.enabled = PARALLEL_CFG.get("enabled", False)
        self.models = PARALLEL_CFG.get("models", {"claude_kernel_generator": 2, "o3_kernel_generator": 2})
        self.selection_strategy = PARALLEL_CFG.get("selection_strategy", "best_overall")
        self.timeout = PARALLEL_CFG.get("timeout", 120)
        self.max_retries = PARALLEL_CFG.get("max_retries", 2)
        
        # Create generators for each model type
        self.generators = {}
        for agent_name in self.models.keys():
            self.generators[agent_name] = KernelGenerator(kernel_lang=kernel_lang)
            # Override the agent name for model selection
            self.generators[agent_name].role = agent_name
            
        # Executor for testing candidates
        self.executor = Executor(kernel_lang=kernel_lang)
        
        print(f"Parallel Kernel Generator initialized:")
        print(f"  Enabled: {self.enabled}")
        print(f"  Models: {self.models}")
        print(f"  Selection strategy: {self.selection_strategy}")
        print(f"  Timeout: {self.timeout}s")
    
    def _generate_single_candidate(
        self,
        agent_name: str,
        instance_id: int,
        torch_expl: str,
        doc_context: str,
        feedback: str,
        iter_idx: int,
        previous_kernel: str
    ) -> KernelCandidate:
        """Generate a single kernel candidate"""
        start_time = time.time()
        
        try:
            # Generate kernel code
            generator = self.generators[agent_name]
            
            # Build code input
            code_input = torch_expl
            
            kernel_code = generator.generate(
                torch_expl=code_input,
                doc_context=doc_context,
                feedback=feedback,
                iter_idx=iter_idx,
                previous_kernel=previous_kernel
            )
            
            generation_time = time.time() - start_time
            
            candidate = KernelCandidate(
                code=kernel_code,
                agent_name=agent_name,
                instance_id=instance_id,
                generation_time=generation_time
            )
            
            log.append({
                "event": "parallel_candidate_generated",
                "agent": agent_name,
                "instance": instance_id,
                "generation_time": generation_time,
                "code_length": len(kernel_code)
            })
            
            return candidate
            
        except Exception as e:
            generation_time = time.time() - start_time
            candidate = KernelCandidate(
                code="",
                agent_name=agent_name,
                instance_id=instance_id,
                generation_time=generation_time,
                errors=f"Generation failed: {str(e)}"
            )
            
            log.append({
                "event": "parallel_candidate_failed",
                "agent": agent_name,
                "instance": instance_id,
                "error": str(e),
                "generation_time": generation_time
            })
            
            return candidate
    
    def _evaluate_candidate(
        self,
        candidate: KernelCandidate,
        torch_file: str,
        baseline_us: float
    ) -> KernelCandidate:
        """Evaluate a kernel candidate for compilation, runtime, and correctness"""
        
        if not candidate.code.strip():
            candidate.errors = "Empty kernel code"
            return candidate
            
        try:
            # Test compilation and execution
            stats, errors, kernel_file = self.executor.run(candidate.code)
            
            candidate.compile_success = kernel_file is not None
            candidate.runtime_success = errors is None or errors == ""
            candidate.errors = errors or ""
            candidate.stats = stats
            
            if candidate.runtime_success:
                # Extract performance metrics
                candidate.latency_us = _extract_latency_us(stats)
                if candidate.latency_us and baseline_us > 0:
                    candidate.speedup = baseline_us / candidate.latency_us
                
                # Test correctness if enabled
                if CHK_NUM and kernel_file:
                    try:
                        kernel_dir = Path(kernel_file).parent
                        so_path = kernel_dir / "kernel.so"
                        
                        if so_path.exists():
                            err = max_abs_err(torch_file, str(so_path))
                            candidate.correctness_pass = err <= ATOL
                            if not candidate.correctness_pass:
                                candidate.errors += f" MAX_ABS_ERR={err:.4e} > {ATOL}"
                        else:
                            candidate.errors += " Shared library not found"
                            
                    except Exception as e:
                        candidate.errors += f" Correctness check failed: {str(e)}"
                        
            # Recalculate score with new information
            candidate.calculate_score()
            
            log.append({
                "event": "parallel_candidate_evaluated",
                "agent": candidate.agent_name,
                "instance": candidate.instance_id,
                "compile_success": candidate.compile_success,
                "runtime_success": candidate.runtime_success,
                "correctness_pass": candidate.correctness_pass,
                "latency_us": candidate.latency_us,
                "speedup": candidate.speedup,
                "score": candidate.score,
                "errors": candidate.errors
            })
            
        except Exception as e:
            candidate.errors = f"Evaluation failed: {str(e)}"
            log.append({
                "event": "parallel_candidate_eval_failed",
                "agent": candidate.agent_name,
                "instance": candidate.instance_id,
                "error": str(e)
            })
        
        return candidate
    
    def _select_best_candidate(
        self,
        candidates: List[KernelCandidate],
        baseline_us: float
    ) -> Tuple[KernelCandidate, Dict[str, Any]]:
        """Select the best candidate based on the configured strategy"""
        
        if not candidates:
            raise ValueError("No candidates to select from")
        
        # Filter out failed generations
        valid_candidates = [c for c in candidates if c.code.strip()]
        
        if not valid_candidates:
            # Return the least-failed candidate
            return min(candidates, key=lambda c: len(c.errors)), {
                "selection_strategy": self.selection_strategy,
                "total_candidates": len(candidates),
                "valid_candidates": 0,
                "reason": "no_valid_candidates"
            }
        
        selection_info = {
            "selection_strategy": self.selection_strategy,
            "total_candidates": len(candidates),
            "valid_candidates": len(valid_candidates),
            "candidate_scores": [c.score for c in valid_candidates]
        }
        
        if self.selection_strategy == "best_overall":
            # Select based on combined score
            best = max(valid_candidates, key=lambda c: c.score)
            selection_info["reason"] = "highest_combined_score"
            
        elif self.selection_strategy == "best_correct":
            # Select best among correct candidates, fallback to best overall
            correct_candidates = [c for c in valid_candidates if c.correctness_pass]
            if correct_candidates:
                best = max(correct_candidates, key=lambda c: c.score)
                selection_info["reason"] = "best_among_correct"
                selection_info["correct_candidates"] = len(correct_candidates)
            else:
                best = max(valid_candidates, key=lambda c: c.score)
                selection_info["reason"] = "no_correct_fallback_to_best"
                selection_info["correct_candidates"] = 0
                
        elif self.selection_strategy == "fastest_correct":
            # Select fastest among correct candidates
            correct_candidates = [c for c in valid_candidates 
                                if c.correctness_pass and c.latency_us is not None]
            if correct_candidates:
                best = min(correct_candidates, key=lambda c: c.latency_us)
                selection_info["reason"] = "fastest_among_correct"
                selection_info["correct_candidates"] = len(correct_candidates)
            else:
                # Fallback to best correct or best overall
                correct_candidates = [c for c in valid_candidates if c.correctness_pass]
                if correct_candidates:
                    best = max(correct_candidates, key=lambda c: c.score)
                    selection_info["reason"] = "best_correct_no_perf"
                else:
                    best = max(valid_candidates, key=lambda c: c.score)
                    selection_info["reason"] = "no_correct_fallback_to_best"
                selection_info["correct_candidates"] = len(correct_candidates)
        else:
            # Default to best overall
            best = max(valid_candidates, key=lambda c: c.score)
            selection_info["reason"] = "default_best_overall"
        
        selection_info["selected_agent"] = best.agent_name
        selection_info["selected_instance"] = best.instance_id
        selection_info["selected_score"] = best.score
        
        return best, selection_info
    
    def generate_parallel(
        self,
        torch_expl: str,
        doc_context: str,
        feedback: str = "",
        iter_idx: int = 0,
        previous_kernel: str = "",
        torch_file: str = "",
        baseline_us: float = 0.0
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Generate multiple kernel candidates in parallel and return the best one.
        
        Returns:
            Tuple of (best_kernel_code, selection_metadata)
        """
        
        if not self.enabled:
            # Fallback to single generator - prefer Claude, then any available
            generator = self.generators.get("claude_kernel_generator", 
                                          self.generators.get("o3_kernel_generator",
                                          list(self.generators.values())[0]))
            code = generator.generate(torch_expl, doc_context, feedback, iter_idx, previous_kernel)
            return code, {"parallel_enabled": False, "fallback": True}
        
        start_time = time.time()
        
        # Create list of generation tasks
        tasks = []
        for agent_name, count in self.models.items():
            if agent_name in self.generators:
                for instance_id in range(count):
                    tasks.append((agent_name, instance_id))
        
        print(f"Starting parallel generation with {len(tasks)} tasks...")
        
        # Generate candidates in parallel
        candidates = []
        with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
            # Submit generation tasks
            future_to_task = {
                executor.submit(
                    self._generate_single_candidate,
                    agent_name, instance_id, torch_expl, doc_context,
                    feedback, iter_idx, previous_kernel
                ): (agent_name, instance_id)
                for agent_name, instance_id in tasks
            }
            
            # Collect results with timeout
            for future in as_completed(future_to_task, timeout=self.timeout):
                try:
                    candidate = future.result(timeout=10)  # Individual timeout
                    candidates.append(candidate)
                except Exception as e:
                    agent_name, instance_id = future_to_task[future]
                    error_candidate = KernelCandidate(
                        code="",
                        agent_name=agent_name,
                        instance_id=instance_id,
                        generation_time=0.0,
                        errors=f"Future failed: {str(e)}"
                    )
                    candidates.append(error_candidate)
        
        generation_time = time.time() - start_time
        print(f"Parallel generation completed in {generation_time:.2f}s")
        
        # Evaluate candidates in parallel if we have valid ones
        valid_candidates = [c for c in candidates if c.code.strip()]
        
        if valid_candidates and torch_file and baseline_us > 0:
            print(f"Evaluating {len(valid_candidates)} valid candidates...")
            eval_start_time = time.time()
            
            with ThreadPoolExecutor(max_workers=min(4, len(valid_candidates))) as executor:
                # Submit evaluation tasks
                future_to_candidate = {
                    executor.submit(self._evaluate_candidate, candidate, torch_file, baseline_us): candidate
                    for candidate in valid_candidates
                }
                
                # Update candidates with evaluation results
                evaluated_candidates = []
                for future in as_completed(future_to_candidate, timeout=self.timeout):
                    try:
                        evaluated_candidate = future.result(timeout=30)
                        evaluated_candidates.append(evaluated_candidate)
                    except Exception as e:
                        # Keep original candidate if evaluation fails
                        candidate = future_to_candidate[future]
                        candidate.errors += f" Evaluation timeout: {str(e)}"
                        evaluated_candidates.append(candidate)
                
                # Replace valid candidates with evaluated ones
                # Keep failed generation candidates as-is
                candidate_map = {id(c): c for c in valid_candidates}
                for eval_c in evaluated_candidates:
                    for i, orig_c in enumerate(candidates):
                        if id(orig_c) in candidate_map and orig_c.agent_name == eval_c.agent_name and orig_c.instance_id == eval_c.instance_id:
                            candidates[i] = eval_c
                            break
            
            eval_time = time.time() - eval_start_time
            print(f"Candidate evaluation completed in {eval_time:.2f}s")
        
        # Select best candidate
        best_candidate, selection_info = self._select_best_candidate(candidates, baseline_us)
        
        total_time = time.time() - start_time
        
        # Create comprehensive metadata
        metadata = {
            "parallel_enabled": True,
            "total_time": total_time,
            "generation_time": generation_time,
            "total_tasks": len(tasks),
            "successful_generations": len([c for c in candidates if c.code.strip()]),
            "candidates": [
                {
                    "agent": c.agent_name,
                    "instance": c.instance_id,
                    "success": bool(c.code.strip()),
                    "compile_success": c.compile_success,
                    "runtime_success": c.runtime_success,
                    "correctness_pass": c.correctness_pass,
                    "latency_us": c.latency_us,
                    "speedup": c.speedup,
                    "score": c.score,
                    "generation_time": c.generation_time,
                    "errors": c.errors[:200] + "..." if len(c.errors) > 200 else c.errors
                }
                for c in candidates
            ],
            **selection_info
        }
        
        # Log summary
        log.append({
            "event": "parallel_generation_complete",
            "metadata": metadata,
            "selected_code_length": len(best_candidate.code),
            "best_candidate": {
                "agent": best_candidate.agent_name,
                "instance": best_candidate.instance_id,
                "score": best_candidate.score,
                "compile_success": best_candidate.compile_success,
                "runtime_success": best_candidate.runtime_success,
                "correctness_pass": best_candidate.correctness_pass,
                "latency_us": best_candidate.latency_us,
                "speedup": best_candidate.speedup
            }
        })
        
        print(f"Selected best candidate: {best_candidate.agent_name}#{best_candidate.instance_id} (score: {best_candidate.score:.2f})")
        
        return best_candidate.code, metadata

    def generate(
        self,
        torch_expl: str,
        doc_context: str,
        feedback: str = "",
        iter_idx: int = 0,
        previous_kernel: str = "",
        torch_file: str = "",
        baseline_us: float = 0.0
    ) -> str:
        """
        Main interface that decides between parallel and single generation.
        Compatible with the original KernelGenerator interface.
        """
        if self.enabled:
            code, metadata = self.generate_parallel(
                torch_expl, doc_context, feedback, iter_idx, 
                previous_kernel, torch_file, baseline_us
            )
            return code
        else:
            # Use the first available generator as fallback - prefer Claude
            generator = self.generators.get("claude_kernel_generator",
                       self.generators.get("o3_kernel_generator", 
                       list(self.generators.values())[0]))
            return generator.generate(torch_expl, doc_context, feedback, iter_idx, previous_kernel)
