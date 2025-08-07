"""
Enhanced Parallel Kernel Generator with specialized handling for complex kernels
like large matrix multiplications. Includes iterative refinement and Alpha Evolve integration.
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
import math
import re

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
    """Enhanced candidate with complexity analysis"""
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
    score: float = 0.0
    complexity_score: float = 0.0  # New: code complexity analysis
    memory_efficiency: float = 0.0  # New: memory usage analysis
    
    def __post_init__(self):
        """Calculate scores after initialization"""
        self.analyze_complexity()
        self.calculate_score()
    
    def analyze_complexity(self):
        """Analyze code complexity and patterns - generalized for all operation types"""
        if not self.code:
            return
            
        code_lower = self.code.lower()
        
        # === MEMORY MANAGEMENT PATTERNS ===
        shared_mem_score = 0
        if "__shared__" in code_lower or "lds" in code_lower:
            shared_mem_score = 15  # Shared memory usage
        elif "local" in code_lower or "__local" in code_lower:
            shared_mem_score = 10  # Local memory usage
        
        # === MEMORY ACCESS PATTERNS ===
        coalescing_score = 0
        if any(pattern in code_lower for pattern in ["coalesced", "stride", "contiguous"]):
            coalescing_score = 8
        if any(pattern in code_lower for pattern in ["cache", "prefetch"]):
            coalescing_score += 5
        
        # === ALGORITHMIC COMPLEXITY ===
        algorithm_score = 0
        
        # Tiling and blocking strategies
        if any(word in code_lower for word in ["tile", "block", "chunk"]):
            algorithm_score += 15
        
        # Mathematical optimizations
        if any(pattern in code_lower for pattern in ["fma", "mad", "dot", "gemm"]):
            algorithm_score += 8
        if any(pattern in code_lower for pattern in ["approx", "fast", "rsqrt"]):
            algorithm_score += 5
        
        # Reduction and scan operations
        if any(pattern in code_lower for pattern in ["reduce", "scan", "prefix", "tree"]):
            algorithm_score += 10
        
        # Convolution-specific patterns
        if any(pattern in code_lower for pattern in ["winograd", "im2col", "separable"]):
            algorithm_score += 12
        
        # Advanced mathematical functions
        if any(pattern in code_lower for pattern in ["exp", "log", "sqrt", "sin", "cos", "tanh"]):
            algorithm_score += 6
        
        # === THREAD AND EXECUTION PATTERNS ===
        threading_score = 0
        thread_patterns = ["blockdim", "threadsperblock", "gridsize", "workgroup", "warp", "wavefront"]
        if any(pattern in code_lower for pattern in thread_patterns):
            threading_score = 12
        
        # Synchronization patterns
        if any(pattern in code_lower for pattern in ["barrier", "sync", "__syncthreads"]):
            threading_score += 5
        
        # Warp/wavefront primitives
        if any(pattern in code_lower for pattern in ["shuffle", "ballot", "reduce_add", "reduce_max"]):
            threading_score += 8
        
        # === VECTORIZATION AND SIMD ===
        vectorization_score = 0
        if any(pattern in code_lower for pattern in ["vector", "simd", "packed"]):
            vectorization_score = 8
        if any(pattern in code_lower for pattern in ["float4", "int4", "double2"]):
            vectorization_score += 6
        
        # === CODE STRUCTURE QUALITY ===
        structure_score = 0
        lines = self.code.split('\n')
        
        # Control flow complexity
        if "for" in code_lower and "if" in code_lower:
            structure_score += 6
        if "while" in code_lower:
            structure_score += 3
        
        # Code length and sophistication
        if len(lines) > 50:
            structure_score += 8  # Substantial implementation
        elif len(lines) > 20:
            structure_score += 5  # Moderate implementation
        
        # Function decomposition
        function_count = code_lower.count("__device__") + code_lower.count("__host__")
        structure_score += min(6, function_count * 2)
        
        # Comments and documentation
        comment_lines = sum(1 for line in lines if line.strip().startswith('//') or '/*' in line)
        structure_score += min(4, comment_lines)
        
        # === OPERATION-SPECIFIC PATTERNS ===
        operation_specific_score = 0
        
        # Matrix operations
        if any(pattern in code_lower for pattern in ["transpose", "triangular", "symmetric"]):
            operation_specific_score += 6
        
        # Convolution operations
        if any(pattern in code_lower for pattern in ["kernel", "filter", "channel", "spatial"]):
            operation_specific_score += 5
        
        # Normalization operations
        if any(pattern in code_lower for pattern in ["mean", "variance", "std", "norm"]):
            operation_specific_score += 5
        
        # Activation functions
        if any(pattern in code_lower for pattern in ["activation", "nonlinear", "sigmoid", "relu"]):
            operation_specific_score += 4
        
        # === TOTAL COMPLEXITY SCORE ===
        self.complexity_score = (
            shared_mem_score + 
            coalescing_score + 
            algorithm_score + 
            threading_score + 
            vectorization_score + 
            structure_score + 
            operation_specific_score
        )
        
        # === MEMORY EFFICIENCY ANALYSIS ===
        total_lines = len(lines)
        if total_lines > 0:
            # Count memory-related operations
            memory_ops = sum(1 for line in lines if any(op in line.lower() 
                           for op in ['malloc', 'free', 'shared', 'global', 'local', 
                                    'texture', 'constant', '__restrict__']))
            
            # Count optimization patterns
            optimization_ops = sum(1 for line in lines if any(op in line.lower()
                                 for op in ['prefetch', 'cache', 'align', 'vectorize',
                                          'unroll', 'pipeline']))
            
            self.memory_efficiency = min(25, (memory_ops * 2) + (optimization_ops * 3))
    
    def calculate_score(self):
        """Enhanced scoring with complexity consideration"""
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
            import math
            score += min(150, 50 * math.log(self.speedup + 1))
            
        # Latency penalty: up to -50 points
        if self.latency_us and self.latency_us > 0:
            normalized_latency = min(1.0, self.latency_us / 10000.0)
            score -= 50 * normalized_latency
            
        # Generation time bonus: up to +20 points
        if self.generation_time > 0:
            time_bonus = max(0, 20 * (1 - min(1.0, self.generation_time / 30.0)))
            score += time_bonus
        
        # NEW: Complexity bonus: up to +50 points for sophisticated code
        score += min(50, self.complexity_score)
        
        # NEW: Memory efficiency bonus: up to +30 points
        score += min(30, self.memory_efficiency)
            
        self.score = score


class EnhancedParallelKernelGenerator:
    """
    Enhanced parallel generator with specialized handling for complex kernels
    and integration capabilities for Alpha Evolve
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
        
        # Enhanced settings for complex kernels
        self.enhanced_mode = PARALLEL_CFG.get("enhanced_mode", True)
        self.iterative_refinement = PARALLEL_CFG.get("iterative_refinement", True)
        self.complexity_threshold = PARALLEL_CFG.get("complexity_threshold", 30.0)
        
        # Alpha Evolve integration settings
        self.alpha_evolve_enabled = PARALLEL_CFG.get("alpha_evolve", {}).get("enabled", False)
        self.alpha_evolve_models = PARALLEL_CFG.get("alpha_evolve", {}).get("models", 2)
        
        # Create generators for each model type
        self.generators = {}
        for agent_name in self.models.keys():
            self.generators[agent_name] = KernelGenerator(kernel_lang=kernel_lang)
            self.generators[agent_name].role = agent_name
            
        # Executor for testing candidates
        self.executor = Executor(kernel_lang=kernel_lang)
        
        print(f"Enhanced Parallel Kernel Generator initialized:")
        print(f"  Enabled: {self.enabled}")
        print(f"  Models: {self.models}")
        print(f"  Enhanced mode: {self.enhanced_mode}")
        print(f"  Iterative refinement: {self.iterative_refinement}")
        print(f"  Alpha Evolve: {self.alpha_evolve_enabled}")
    
    def _detect_kernel_complexity(self, torch_expl: str) -> Dict[str, Any]:
        """Analyze the complexity of the requested kernel - generalized for all operation types"""
        explanation_lower = torch_expl.lower()
        
        complexity_indicators = {
            "operation_category": "unknown",
            "operation_type": "unknown",
            "estimated_complexity": "low",
            "memory_bound": False,
            "compute_bound": False,
            "data_dimensions": [],
            "optimization_hints": [],
            "complexity_factors": {}
        }
        
        # === MATRIX OPERATIONS ===
        if any(op in explanation_lower for op in ["matmul", "matrix multiplication", "gemm", "dot product"]):
            complexity_indicators["operation_category"] = "matrix_ops"
            complexity_indicators["operation_type"] = "matmul"
            
            # Extract matrix dimensions
            size_patterns = [r'(\d+)x(\d+)', r'(\d+) x (\d+)', r'size.*?(\d+)', r'shape.*?(\d+)']
            max_dim = 0
            for pattern in size_patterns:
                matches = re.findall(pattern, torch_expl)
                if matches:
                    for match in matches:
                        if isinstance(match, tuple):
                            dim = max(int(x) for x in match)
                        else:
                            dim = int(match)
                        max_dim = max(max_dim, dim)
                        complexity_indicators["data_dimensions"].append(dim)
            
            if max_dim > 1024:
                complexity_indicators["estimated_complexity"] = "high"
                complexity_indicators["memory_bound"] = True
                complexity_indicators["optimization_hints"] = ["tiling", "shared_memory", "coalescing", "register_blocking"]
            elif max_dim > 256:
                complexity_indicators["estimated_complexity"] = "medium"
                complexity_indicators["compute_bound"] = True
                complexity_indicators["optimization_hints"] = ["vectorization", "shared_memory", "thread_optimization"]
            
            complexity_indicators["complexity_factors"]["matrix_size"] = max_dim
            
        # === CONVOLUTION OPERATIONS ===
        elif any(op in explanation_lower for op in ["conv", "convolution"]):
            complexity_indicators["operation_category"] = "convolution"
            
            # Detect convolution type
            if "depthwise" in explanation_lower:
                complexity_indicators["operation_type"] = "depthwise_conv"
                complexity_indicators["optimization_hints"] = ["channel_parallelism", "spatial_tiling"]
            elif "separable" in explanation_lower:
                complexity_indicators["operation_type"] = "separable_conv"
                complexity_indicators["optimization_hints"] = ["factored_kernels", "channel_optimization"]
            elif "transposed" in explanation_lower:
                complexity_indicators["operation_type"] = "transposed_conv"
                complexity_indicators["optimization_hints"] = ["inverse_mapping", "memory_layout"]
            elif "pointwise" in explanation_lower:
                complexity_indicators["operation_type"] = "pointwise_conv"
                complexity_indicators["optimization_hints"] = ["vectorization", "channel_parallelism"]
            else:
                complexity_indicators["operation_type"] = "standard_conv"
            
            # Detect dimensionality
            if "3d" in explanation_lower:
                complexity_indicators["estimated_complexity"] = "high"
                complexity_indicators["memory_bound"] = True
                complexity_indicators["optimization_hints"].extend(["3d_tiling", "temporal_blocking"])
            elif "2d" in explanation_lower:
                complexity_indicators["estimated_complexity"] = "medium"
                complexity_indicators["compute_bound"] = True
                complexity_indicators["optimization_hints"].extend(["spatial_tiling", "im2col"])
            elif "1d" in explanation_lower:
                complexity_indicators["estimated_complexity"] = "low"
                complexity_indicators["optimization_hints"].extend(["vectorization", "sliding_window"])
            
            # Detect special properties
            if any(prop in explanation_lower for prop in ["stride", "strided"]):
                complexity_indicators["optimization_hints"].append("strided_access")
            if any(prop in explanation_lower for prop in ["pad", "padding"]):
                complexity_indicators["optimization_hints"].append("boundary_handling")
            if any(prop in explanation_lower for prop in ["dilation", "dilated"]):
                complexity_indicators["optimization_hints"].append("dilated_kernels")
            if any(prop in explanation_lower for prop in ["group", "grouped"]):
                complexity_indicators["optimization_hints"].append("group_convolution")
        
        # === ACTIVATION FUNCTIONS ===
        elif any(op in explanation_lower for op in ["relu", "gelu", "sigmoid", "tanh", "swish", "elu", "selu"]):
            complexity_indicators["operation_category"] = "activation"
            
            if "gelu" in explanation_lower:
                complexity_indicators["operation_type"] = "gelu"
                complexity_indicators["estimated_complexity"] = "medium"
                complexity_indicators["optimization_hints"] = ["mathematical_approximation", "vectorization", "fused_ops"]
            elif "swish" in explanation_lower:
                complexity_indicators["operation_type"] = "swish"
                complexity_indicators["estimated_complexity"] = "medium"
                complexity_indicators["optimization_hints"] = ["fused_sigmoid_mul", "vectorization"]
            elif "relu" in explanation_lower:
                complexity_indicators["operation_type"] = "relu"
                complexity_indicators["estimated_complexity"] = "low"
                complexity_indicators["optimization_hints"] = ["vectorization", "fused_ops", "conditional_optimization"]
            else:
                complexity_indicators["estimated_complexity"] = "low"
                complexity_indicators["optimization_hints"] = ["vectorization", "mathematical_optimization"]
        
        # === NORMALIZATION OPERATIONS ===
        elif any(op in explanation_lower for op in ["norm", "batchnorm", "layernorm", "groupnorm", "rmsnorm"]):
            complexity_indicators["operation_category"] = "normalization"
            
            if "layernorm" in explanation_lower:
                complexity_indicators["operation_type"] = "layernorm"
                complexity_indicators["estimated_complexity"] = "medium"
                complexity_indicators["optimization_hints"] = ["welford_algorithm", "reduction_optimization", "fused_ops"]
            elif "batchnorm" in explanation_lower:
                complexity_indicators["operation_type"] = "batchnorm"
                complexity_indicators["estimated_complexity"] = "medium"
                complexity_indicators["optimization_hints"] = ["batch_reduction", "running_statistics", "fused_ops"]
            elif "groupnorm" in explanation_lower:
                complexity_indicators["operation_type"] = "groupnorm"
                complexity_indicators["estimated_complexity"] = "medium"
                complexity_indicators["optimization_hints"] = ["group_reduction", "channel_grouping"]
            elif "rmsnorm" in explanation_lower:
                complexity_indicators["operation_type"] = "rmsnorm"
                complexity_indicators["estimated_complexity"] = "low"
                complexity_indicators["optimization_hints"] = ["rms_calculation", "vectorization"]
        
        # === POOLING OPERATIONS ===
        elif any(op in explanation_lower for op in ["pool", "pooling"]):
            complexity_indicators["operation_category"] = "pooling"
            
            if "max" in explanation_lower:
                complexity_indicators["operation_type"] = "max_pooling"
                complexity_indicators["optimization_hints"] = ["sliding_window", "reduction_optimization"]
            elif "average" in explanation_lower or "avg" in explanation_lower:
                complexity_indicators["operation_type"] = "avg_pooling"
                complexity_indicators["optimization_hints"] = ["accumulation", "division_optimization"]
            
            # Dimensionality affects complexity
            if "3d" in explanation_lower:
                complexity_indicators["estimated_complexity"] = "medium"
                complexity_indicators["optimization_hints"].append("3d_window")
            else:
                complexity_indicators["estimated_complexity"] = "low"
        
        # === REDUCTION OPERATIONS ===
        elif any(op in explanation_lower for op in ["sum", "mean", "max", "min", "argmax", "argmin", "product"]):
            complexity_indicators["operation_category"] = "reduction"
            
            if "cumsum" in explanation_lower or "cumprod" in explanation_lower:
                complexity_indicators["operation_type"] = "cumulative"
                complexity_indicators["estimated_complexity"] = "medium"
                complexity_indicators["optimization_hints"] = ["prefix_sum", "scan_algorithm", "bank_conflicts"]
            elif any(op in explanation_lower for op in ["argmax", "argmin"]):
                complexity_indicators["operation_type"] = "argreduce"
                complexity_indicators["optimization_hints"] = ["index_tracking", "reduction_trees"]
            else:
                complexity_indicators["operation_type"] = "simple_reduction"
                complexity_indicators["optimization_hints"] = ["reduction_trees", "shared_memory", "warp_primitives"]
            
            # Special cases
            if "masked" in explanation_lower:
                complexity_indicators["optimization_hints"].append("conditional_reduction")
            if "reverse" in explanation_lower:
                complexity_indicators["optimization_hints"].append("reverse_indexing")
            if "exclusive" in explanation_lower:
                complexity_indicators["optimization_hints"].append("exclusive_scan")
        
        # === LOSS FUNCTIONS ===
        elif any(op in explanation_lower for op in ["loss", "mse", "crossentropy", "hinge", "huber", "cosine", "kl", "triplet"]):
            complexity_indicators["operation_category"] = "loss"
            
            if "crossentropy" in explanation_lower:
                complexity_indicators["operation_type"] = "crossentropy_loss"
                complexity_indicators["estimated_complexity"] = "medium"
                complexity_indicators["optimization_hints"] = ["logsumexp", "numerical_stability", "softmax_fusion"]
            elif "triplet" in explanation_lower:
                complexity_indicators["operation_type"] = "triplet_loss"
                complexity_indicators["estimated_complexity"] = "high"
                complexity_indicators["optimization_hints"] = ["distance_computation", "margin_handling", "mining_strategy"]
            elif "cosine" in explanation_lower:
                complexity_indicators["operation_type"] = "cosine_loss"
                complexity_indicators["optimization_hints"] = ["dot_product", "normalization", "vectorization"]
            else:
                complexity_indicators["estimated_complexity"] = "low"
                complexity_indicators["optimization_hints"] = ["elementwise_ops", "reduction"]
        
        # === DEFAULT CASE ===
        else:
            # Try to infer from tensor operations
            if any(op in explanation_lower for op in ["tensor", "element", "broadcast"]):
                complexity_indicators["operation_category"] = "elementwise"
                complexity_indicators["estimated_complexity"] = "low"
                complexity_indicators["optimization_hints"] = ["vectorization", "memory_coalescing", "broadcast_optimization"]
        
        # === COMPLEXITY SCORING ===
        complexity_score = 0
        if complexity_indicators["estimated_complexity"] == "high":
            complexity_score = 80
        elif complexity_indicators["estimated_complexity"] == "medium":
            complexity_score = 50
        else:
            complexity_score = 20
        
        # Add bonus for special optimizations
        complexity_score += len(complexity_indicators["optimization_hints"]) * 2
        
        complexity_indicators["complexity_factors"]["base_score"] = complexity_score
        complexity_indicators["complexity_factors"]["optimization_count"] = len(complexity_indicators["optimization_hints"])
        
        return complexity_indicators
    
    def _generate_enhanced_prompts(
        self,
        torch_expl: str,
        complexity_info: Dict[str, Any],
        doc_context: str,
        iter_idx: int
    ) -> Dict[str, str]:
        """Generate enhanced prompts based on complexity analysis - generalized for all operation types"""
        
        base_prompt = torch_expl
        
        # Get operation-specific guidance
        operation_category = complexity_info.get("operation_category", "unknown")
        operation_type = complexity_info.get("operation_type", "unknown")
        optimization_hints = complexity_info.get("optimization_hints", [])
        estimated_complexity = complexity_info.get("estimated_complexity", "low")
        
        # Add complexity-specific guidance
        complexity_guidance = f"""

                                OPERATION ANALYSIS:
                                - Category: {operation_category}
                                - Type: {operation_type}
                                - Complexity: {estimated_complexity}
                                - Recommended optimizations: {', '.join(optimization_hints)}

                                """
        
        # Add category-specific optimization guidance
        if operation_category == "matrix_ops":
            complexity_guidance += """
                                    MATRIX OPERATION OPTIMIZATIONS:
                                    1. Use shared memory (LDS) for data reuse and cache blocking
                                    2. Implement proper tiling strategy (16x16, 32x32, or 64x64 tiles)
                                    3. Ensure memory coalescing for global memory accesses
                                    4. Use register blocking for inner loops
                                    5. Consider transpose optimizations for memory layout
                                    6. Implement proper boundary checks for non-multiple sizes
                                    """
        elif operation_category == "convolution":
            complexity_guidance += """
                                    CONVOLUTION OPTIMIZATIONS:
                                    1. Use im2col transformation or direct convolution
                                    2. Implement proper spatial and channel tiling
                                    3. Use shared memory for input feature map reuse
                                    4. Consider separable convolution factorization
                                    5. Optimize for stride patterns and padding
                                    6. Use vectorized loads/stores for channels
                                    7. Consider Winograd algorithm for small kernels
                                    """
        elif operation_category == "activation":
            complexity_guidance += """
                                    ACTIVATION FUNCTION OPTIMIZATIONS:
                                    1. Use vectorized operations for SIMD efficiency
                                    2. Implement mathematical approximations for complex functions
                                    3. Fuse with adjacent operations to reduce memory traffic
                                    4. Use lookup tables for expensive transcendental functions
                                    5. Consider branch-free implementations for conditionals
                                    """
        elif operation_category == "normalization":
            complexity_guidance += """
                                    NORMALIZATION OPTIMIZATIONS:
                                    1. Use Welford's algorithm for numerical stability
                                    2. Implement efficient reduction patterns
                                    3. Fuse statistics computation with normalization
                                    4. Use shared memory for cross-thread communication
                                    5. Consider online vs. two-pass algorithms
                                    6. Optimize for different tensor layouts (NCHW vs NHWC)
                                    """
        elif operation_category == "pooling":
            complexity_guidance += """
                                    POOLING OPTIMIZATIONS:
                                    1. Use sliding window with stride optimization
                                    2. Implement efficient reduction for max/avg operations
                                    3. Use shared memory for overlapping windows
                                    4. Consider spatial locality for memory access
                                    5. Optimize boundary handling for edge cases
                                    """
        elif operation_category == "reduction":
            complexity_guidance += """
                                REDUCTION OPTIMIZATIONS:
                                1. Use tree reduction patterns for efficiency
                                2. Implement warp-level primitives when available
                                3. Use shared memory for intermediate results
                                4. Consider butterfly reduction patterns
                                5. Handle bank conflicts in shared memory
                                6. Optimize for different reduction dimensions
                                """
        elif operation_category == "loss":
            complexity_guidance += """
                                LOSS FUNCTION OPTIMIZATIONS:
                                1. Ensure numerical stability for log operations
                                2. Fuse softmax with cross-entropy when applicable
                                3. Use efficient distance computations
                                4. Consider memory layout for batch processing
                                5. Implement stable algorithms for edge cases
                                """
        else:  # elementwise or unknown
            complexity_guidance += """
                                    GENERAL OPTIMIZATIONS:
                                    1. Use vectorized operations for SIMD efficiency
                                    2. Ensure memory coalescing for global accesses
                                    3. Consider loop unrolling and fusion opportunities
                                    4. Use shared memory when data reuse is possible
                                    5. Optimize for the specific tensor layouts and strides
                                    """
        
        # Add general performance guidance
        if estimated_complexity == "high":
            complexity_guidance += """

                                HIGH COMPLEXITY CONSIDERATIONS:
                                - Memory bandwidth is likely the bottleneck
                                - Focus on data reuse and cache efficiency
                                - Use advanced tiling and blocking strategies
                                - Consider multi-level memory hierarchy optimization
                                - Implement sophisticated parallel decomposition
                                """
        elif estimated_complexity == "medium":
            complexity_guidance += """

                                    MEDIUM COMPLEXITY CONSIDERATIONS:
                                    - Balance between compute and memory optimization
                                    - Use moderate tiling and vectorization
                                    - Focus on thread-level parallelism
                                    - Consider register pressure optimization
                                    """
        else:
            complexity_guidance += """

                        LOW COMPLEXITY CONSIDERATIONS:
                        - Focus on vectorization and simple parallelization
                        - Minimize overhead from complex optimizations
                        - Use straightforward memory access patterns
                        """
        
        base_prompt += complexity_guidance
        
        # Model-specific prompts
        prompts = {}
        
        # Claude prompt - emphasize correctness and structure
        prompts["claude_kernel_generator"] = base_prompt + f"""

                                            CLAUDE-SPECIFIC GUIDANCE FOR {operation_category.upper()}:
                                            - Prioritize correctness and numerical stability
                                            - Use clear, well-structured code with comprehensive comments
                                            - Implement robust error handling and boundary checks
                                            - Focus on maintainable and readable algorithms
                                            - Ensure proper handling of edge cases and special values
                                            - Use well-established algorithms from literature
                                            """
        
        # o3 prompt - emphasize performance and mathematical precision
        prompts["o3_kernel_generator"] = base_prompt + f"""

                                        O3-SPECIFIC GUIDANCE FOR {operation_category.upper()}:
                                        - Optimize aggressively for maximum performance
                                        - Use advanced mathematical optimizations and approximations
                                        - Implement cutting-edge algorithmic techniques
                                        - Focus on minimal latency and maximum throughput
                                        - Consider hardware-specific optimizations (MI300X/gfx942)
                                        - Use sophisticated memory access patterns and data layouts
                                        - Leverage advanced parallel programming techniques
                                        """
        
        return prompts
    
    def _apply_alpha_evolve_enhancement(
        self,
        candidates: List[KernelCandidate],
        torch_expl: str,
        complexity_info: Dict[str, Any]
    ) -> List[KernelCandidate]:
        """
        Apply Alpha Evolve-style evolutionary improvement to promising candidates
        Generalized for all kernel operation types
        """
        if not self.alpha_evolve_enabled or not candidates:
            return candidates
        
        # Select top candidates for evolution
        valid_candidates = [c for c in candidates if c.compile_success and c.code.strip()]
        if len(valid_candidates) < 2:
            return candidates
        
        # Sort by score and take top candidates
        top_candidates = sorted(valid_candidates, key=lambda c: c.score, reverse=True)[:2]
        
        evolved_candidates = []
        
        operation_category = complexity_info.get("operation_category", "unknown")
        operation_type = complexity_info.get("operation_type", "unknown")
        optimization_hints = complexity_info.get("optimization_hints", [])
        
        for i, candidate in enumerate(top_candidates):
            try:
                # Create operation-specific evolution prompt
                evolution_prompt = f"""
                                    You are an expert kernel optimization system similar to Alpha Evolve.
                                    Given this working {operation_category} kernel, evolve it to be more efficient while maintaining correctness.

                                    OPERATION CONTEXT:
                                    - Category: {operation_category}
                                    - Type: {operation_type}
                                    - Suggested optimizations: {', '.join(optimization_hints)}

                                    CURRENT KERNEL:
                                    ```
                                    {candidate.code}
                                    ```

                                    EVOLUTION OBJECTIVES:
                                    Maintain functional correctness while applying algorithmic improvements:

                                    """
                
                # Add operation-specific evolution strategies
                if operation_category == "matrix_ops":
                    evolution_prompt += """
                                        MATRIX OPERATION EVOLUTION STRATEGIES:
                                        1. Advanced tiling patterns (hierarchical, cache-oblivious, or adaptive)
                                        2. Novel memory access patterns (Z-order, Morton order, or custom layouts)
                                        3. Register blocking optimizations and pipeline improvements
                                        4. Hybrid algorithms combining multiple mathematical approaches
                                        5. Sophisticated prefetching and memory bandwidth optimization
                                        6. Non-obvious parallelization strategies beyond standard blocking
                                        """
                elif operation_category == "convolution":
                    evolution_prompt += """
                                            CONVOLUTION EVOLUTION STRATEGIES:
                                            1. Advanced convolution algorithms (Winograd variants, FFT-based, or custom)
                                            2. Novel tensor layout transformations and data reorganization
                                            3. Sophisticated channel and spatial parallelization
                                            4. Hybrid direct/im2col approaches with dynamic selection
                                            5. Advanced memory tiling with multi-level blocking
                                            6. Innovative kernel fusion and operation scheduling
                                            """
                elif operation_category == "activation":
                    evolution_prompt += """
                                            ACTIVATION FUNCTION EVOLUTION STRATEGIES:
                                            1. Advanced mathematical approximations with higher accuracy
                                            2. Novel vectorization patterns and SIMD optimization
                                            3. Sophisticated fusion with adjacent operations
                                            4. Custom lookup table implementations with interpolation
                                            5. Branch-free algorithmic alternatives
                                            6. Hardware-specific instruction optimization
                                            """
                elif operation_category == "normalization":
                    evolution_prompt += """
                                            NORMALIZATION EVOLUTION STRATEGIES:
                                            1. Advanced numerically stable algorithms (beyond Welford)
                                            2. Novel reduction patterns and communication strategies
                                            3. Sophisticated memory layout optimizations
                                            4. Innovative fusion with activation and other operations
                                            5. Custom precision handling for stability and performance
                                            6. Advanced parallel decomposition strategies
                                            """
                elif operation_category == "pooling":
                    evolution_prompt += """
                                        POOLING EVOLUTION STRATEGIES:
                                        1. Advanced sliding window implementations
                                        2. Novel spatial tiling and memory access patterns
                                        3. Sophisticated reduction algorithm variants
                                        4. Custom boundary handling optimizations
                                        5. Innovative data reuse strategies
                                        6. Hardware-specific vectorization approaches
                                        """
                elif operation_category == "reduction":
                    evolution_prompt += """
                                        REDUCTION EVOLUTION STRATEGIES:
                                        1. Advanced tree reduction variants (segmented, hierarchical)
                                        2. Novel warp-level primitive utilization
                                        3. Sophisticated memory banking and conflict avoidance
                                        4. Custom scan algorithm implementations
                                        5. Advanced parallel prefix computation
                                        6. Innovative load balancing for irregular reductions
                                        """
                elif operation_category == "loss":
                    evolution_prompt += """
                                        LOSS FUNCTION EVOLUTION STRATEGIES:
                                        1. Advanced numerical stability techniques
                                        2. Novel mathematical reformulations
                                        3. Sophisticated batch processing optimizations
                                        4. Custom precision and range handling
                                        5. Innovative memory access patterns for large batches
                                        6. Advanced fusion with gradient computation
                                        """
                else:  # elementwise or unknown
                    evolution_prompt += """
                                        GENERAL EVOLUTION STRATEGIES:
                                        1. Advanced vectorization and SIMD utilization
                                        2. Novel memory access pattern optimizations
                                        3. Sophisticated loop transformations and unrolling
                                        4. Custom data layout and stride handling
                                        5. Innovative parallel decomposition approaches
                                        6. Hardware-specific instruction optimization
                                        """
                
                evolution_prompt += f"""

                                    EVOLUTION GUIDELINES:
                                    - Think beyond conventional optimization approaches
                                    - Consider non-obvious algorithmic improvements
                                    - Focus on the specific characteristics of {operation_type}
                                    - Maintain or improve numerical accuracy
                                    - Target the MI300X (gfx942) architecture specifically
                                    - Generate a kernel that could outperform the original through algorithmic innovation

                                    Generate an evolved version of this kernel with novel optimizations:
                                """
                
                # Use the same agent that generated the original
                generator = self.generators[candidate.agent_name]
                evolved_code = generator.generate(
                    torch_expl=evolution_prompt,
                    doc_context="",
                    feedback="",
                    iter_idx=99,  # Special marker for evolution
                    previous_kernel=""
                )
                
                # Create evolved candidate
                evolved_candidate = KernelCandidate(
                    code=evolved_code,
                    agent_name=f"{candidate.agent_name}_evolved",
                    instance_id=i,
                    generation_time=candidate.generation_time + 5.0  # Approximate additional time
                )
                
                evolved_candidates.append(evolved_candidate)
                
                log.append({
                    "event": "alpha_evolve_generation",
                    "operation_category": operation_category,
                    "operation_type": operation_type,
                    "original_agent": candidate.agent_name,
                    "original_score": candidate.score,
                    "optimization_hints": optimization_hints,
                    "evolved_code_length": len(evolved_code)
                })
                
            except Exception as e:
                log.append({
                    "event": "alpha_evolve_failed",
                    "operation_category": operation_category,
                    "agent": candidate.agent_name,
                    "error": str(e)
                })
        
        return candidates + evolved_candidates
    
    def generate_enhanced_parallel(
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
        Enhanced parallel generation with complexity analysis and iterative refinement
        """
        
        if not self.enabled:
            # Fallback to single generator
            generator = self.generators.get("claude_kernel_generator", 
                                          list(self.generators.values())[0])
            code = generator.generate(torch_expl, doc_context, feedback, iter_idx, previous_kernel)
            return code, {"parallel_enabled": False, "fallback": True}
        
        start_time = time.time()
        
        # Step 1: Analyze kernel complexity
        complexity_info = self._detect_kernel_complexity(torch_expl)
        print(f"Detected complexity: {complexity_info}")
        
        # Step 2: Generate enhanced prompts if in enhanced mode
        if self.enhanced_mode:
            enhanced_prompts = self._generate_enhanced_prompts(
                torch_expl, complexity_info, doc_context, iter_idx
            )
        else:
            enhanced_prompts = {agent: torch_expl for agent in self.models.keys()}
        
        # Step 3: Generate candidates in parallel
        candidates = []
        tasks = []
        for agent_name, count in self.models.items():
            if agent_name in self.generators:
                prompt = enhanced_prompts.get(agent_name, torch_expl)
                for instance_id in range(count):
                    tasks.append((agent_name, instance_id, prompt))
        
        print(f"Starting enhanced parallel generation with {len(tasks)} tasks...")
        
        with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
            # Submit generation tasks with enhanced prompts
            future_to_task = {
                executor.submit(
                    self._generate_single_candidate,
                    agent_name, instance_id, prompt, doc_context,
                    feedback, iter_idx, previous_kernel
                ): (agent_name, instance_id)
                for agent_name, instance_id, prompt in tasks
            }
            
            # Collect results with timeout
            for future in as_completed(future_to_task, timeout=self.timeout):
                try:
                    candidate = future.result(timeout=15)
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
        print(f"Enhanced parallel generation completed in {generation_time:.2f}s")
        
        # Step 4: Apply Alpha Evolve enhancement if enabled
        if self.alpha_evolve_enabled:
            print("Applying Alpha Evolve enhancements...")
            candidates = self._apply_alpha_evolve_enhancement(candidates, torch_expl, complexity_info)
        
        # Step 5: Evaluate candidates
        valid_candidates = [c for c in candidates if c.code.strip()]
        
        if valid_candidates and torch_file and baseline_us > 0:
            print(f"Evaluating {len(valid_candidates)} valid candidates...")
            eval_start_time = time.time()
            
            with ThreadPoolExecutor(max_workers=min(6, len(valid_candidates))) as executor:
                future_to_candidate = {
                    executor.submit(self._evaluate_candidate, candidate, torch_file, baseline_us): candidate
                    for candidate in valid_candidates
                }
                
                evaluated_candidates = []
                for future in as_completed(future_to_candidate, timeout=self.timeout):
                    try:
                        evaluated_candidate = future.result(timeout=45)
                        evaluated_candidates.append(evaluated_candidate)
                    except Exception as e:
                        candidate = future_to_candidate[future]
                        candidate.errors += f" Evaluation timeout: {str(e)}"
                        evaluated_candidates.append(candidate)
                
                # Replace candidates with evaluated ones
                candidate_map = {id(c): c for c in valid_candidates}
                for eval_c in evaluated_candidates:
                    for i, orig_c in enumerate(candidates):
                        if id(orig_c) in candidate_map and orig_c.agent_name == eval_c.agent_name and orig_c.instance_id == eval_c.instance_id:
                            candidates[i] = eval_c
                            break
            
            eval_time = time.time() - eval_start_time
            print(f"Candidate evaluation completed in {eval_time:.2f}s")
        
        # Step 6: Select best candidate with enhanced scoring
        best_candidate, selection_info = self._select_best_candidate(candidates, baseline_us)
        
        total_time = time.time() - start_time
        
        # Create comprehensive metadata
        metadata = {
            "enhanced_parallel_enabled": True,
            "complexity_analysis": complexity_info,
            "total_time": total_time,
            "generation_time": generation_time,
            "alpha_evolve_applied": self.alpha_evolve_enabled,
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
                    "complexity_score": c.complexity_score,
                    "memory_efficiency": c.memory_efficiency,
                    "generation_time": c.generation_time,
                    "errors": c.errors[:200] + "..." if len(c.errors) > 200 else c.errors
                }
                for c in candidates
            ],
            **selection_info
        }
        
        # Log comprehensive results
        log.append({
            "event": "enhanced_parallel_generation_complete",
            "metadata": metadata,
            "best_candidate": {
                "agent": best_candidate.agent_name,
                "score": best_candidate.score,
                "complexity_score": best_candidate.complexity_score,
                "memory_efficiency": best_candidate.memory_efficiency
            }
        })
        
        print(f"Selected best candidate: {best_candidate.agent_name}#{best_candidate.instance_id}")
        print(f"  Score: {best_candidate.score:.2f}")
        print(f"  Complexity: {best_candidate.complexity_score:.2f}")
        print(f"  Memory efficiency: {best_candidate.memory_efficiency:.2f}")
        
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
        Main generate method that orchestrator expects.
        This delegates to the enhanced parallel generation.
        """
        if not self.enabled:
            # Fallback to single generator
            generator = self.generators.get("claude_kernel_generator", 
                                          list(self.generators.values())[0])
            return generator.generate(torch_expl, doc_context, feedback, iter_idx, previous_kernel)
        
        # Use enhanced parallel generation
        code, metadata = self.generate_enhanced_parallel(
            torch_expl=torch_expl,
            doc_context=doc_context,
            feedback=feedback,
            iter_idx=iter_idx,
            previous_kernel=previous_kernel,
            torch_file=torch_file,
            baseline_us=baseline_us
        )
        
        return code
    
    # Include all the helper methods from the original ParallelKernelGenerator
    def _generate_single_candidate(self, agent_name, instance_id, torch_expl, doc_context, feedback, iter_idx, previous_kernel):
        """Generate a single kernel candidate (same as original)"""
        start_time = time.time()
        
        try:
            generator = self.generators[agent_name]
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
            return candidate
    
    def _evaluate_candidate(self, candidate, torch_file, baseline_us):
        """Evaluate a kernel candidate (enhanced version)"""
        if not candidate.code.strip():
            candidate.errors = "Empty kernel code"
            return candidate
            
        try:
            stats, errors, kernel_file = self.executor.run(candidate.code)
            
            candidate.compile_success = kernel_file is not None
            candidate.runtime_success = errors is None or errors == ""
            candidate.errors = errors or ""
            candidate.stats = stats
            
            if candidate.runtime_success:
                # Extract performance metrics
                def _extract_latency_us(stats):
                    if not stats:
                        return None
                    val = stats.get("avg_us")
                    try:
                        return float(val) if val and val > 0 else None
                    except (TypeError, ValueError):
                        return None
                
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
            
        except Exception as e:
            candidate.errors = f"Evaluation failed: {str(e)}"
        
        return candidate
    
    def _select_best_candidate(self, candidates, baseline_us):
        """Select best candidate (same logic as original)"""
        if not candidates:
            raise ValueError("No candidates to select from")
        
        valid_candidates = [c for c in candidates if c.code.strip()]
        
        if not valid_candidates:
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
            best = max(valid_candidates, key=lambda c: c.score)
            selection_info["reason"] = "highest_combined_score"
        elif self.selection_strategy == "best_correct":
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
            correct_candidates = [c for c in valid_candidates 
                                if c.correctness_pass and c.latency_us is not None]
            if correct_candidates:
                best = min(correct_candidates, key=lambda c: c.latency_us)
                selection_info["reason"] = "fastest_among_correct"
                selection_info["correct_candidates"] = len(correct_candidates)
            else:
                correct_candidates = [c for c in valid_candidates if c.correctness_pass]
                if correct_candidates:
                    best = max(correct_candidates, key=lambda c: c.score)
                    selection_info["reason"] = "best_correct_no_perf"
                else:
                    best = max(valid_candidates, key=lambda c: c.score)
                    selection_info["reason"] = "no_correct_fallback_to_best"
                selection_info["correct_candidates"] = len(correct_candidates)
        else:
            best = max(valid_candidates, key=lambda c: c.score)
            selection_info["reason"] = "default_best_overall"
        
        selection_info["selected_agent"] = best.agent_name
        selection_info["selected_instance"] = best.instance_id
        selection_info["selected_score"] = best.score
        
        return best, selection_info
