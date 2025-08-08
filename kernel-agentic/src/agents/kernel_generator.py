from .base_agent import BaseAgent
from ..utils.llm_client import chat
from ..utils.gpu_specs import get_gpu_specs
from ..utils.logger import log
import yaml, random
import os 
from pathlib import Path
from pathlib import Path
import re
_PROMPT_DIR = Path(__file__).parent

CFG = yaml.safe_load(open("config.yml"))


# Prompt templates loader
def _load_prompt(filename: str) -> str:
    path = os.path.join(os.path.dirname(__file__), os.pardir, "prompts", filename)
    with open(path, 'r') as fp:
        return fp.read()

# Refinement template (language agnostic)
REFINE_PROMPT = _load_prompt("refinement.txt")


class KernelGenerator(BaseAgent):
    """
    Generic KernelGenerator that supports HIP, CUDA, and Triton based on config.
    """
    def __init__(self, kernel_lang: str = None):
        if kernel_lang is None:
            kernel_lang = CFG.get("Pipeline", {}).get("kernel_lang", "hip").lower()
        
        self.kernel_lang = kernel_lang.lower()
        if self.kernel_lang not in ["hip", "cuda", "triton"]:
            raise ValueError(f"Unsupported kernel language: {self.kernel_lang}")

        specs = get_gpu_specs()
        super().__init__(f"{self.kernel_lang}_generator", "<will-be-replaced>")

    def build_user_prompt(self, torch_expl: str, doc_ctx: str, feedback: str, previous_kernel: str = "", iter_idx: int = 0) -> str:
        """Enhanced prompt building with GEAK-Agent inspired structure"""
        lang_map = {
            "hip": "HIP",
            "cuda": "CUDA", 
            "triton": "Triton"
        }
        lang_display = lang_map[self.kernel_lang]
        
        # Stage-specific prompt building
        if iter_idx == 0:
            # Initial generation - focus on correctness
            parts = [
                f"**Task**: Generate a functionally correct {lang_display} kernel implementation.",
                "",
                "**Primary Requirements:**",
                "- Functional correctness takes absolute priority over performance",
                "- Handle all edge cases and boundary conditions",
                "- Implement robust error checking and bounds validation",
                "- Use appropriate numerical precision to avoid accuracy loss",
                "",
                "**PyTorch Operation Analysis:**",
                torch_expl,
                ""
            ]
            
            if doc_ctx.strip():
                parts.extend([
                    f"**{lang_display} Documentation Context:**",
                    doc_ctx,
                    ""
                ])
                
        else:
            # Iterative improvement - focus on optimization and debugging
            parts = [
                f"**Task**: Improve the {lang_display} kernel based on analysis of previous attempts and feedback.",
                "",
                f"**Optimization Stage {iter_idx}:**",
                "- Analyze the previous kernel implementation and feedback",
                "- Apply targeted optimizations while maintaining correctness",
                "- Address specific issues identified in the feedback",
                "- Consider alternative algorithmic approaches if needed",
                "",
                "**PyTorch Operation Analysis:**",
                torch_expl,
                ""
            ]
            
            if previous_kernel.strip():
                parts.extend([
                    f"**Previous {lang_display} Kernel Implementation:**",
                    "```",
                    previous_kernel.strip(),
                    "```",
                    ""
                ])
                
        # Add detailed feedback analysis if available
        if feedback.strip():
            parts.extend([
                "**Execution Feedback and Analysis:**",
                "The following feedback contains critical information about the previous kernel's performance,",
                "correctness, or compilation issues. Analyze this carefully to understand what needs improvement:",
                "",
                feedback[:8000],  # Increased from 6000 to provide more context
                ""
            ])
            
        # Stage-specific instructions
        if iter_idx == 0:
            instruction_parts = [
                "**Generation Instructions:**",
                "1. **Correctness First**: Ensure the kernel produces mathematically correct results",
                "2. **Robust Implementation**: Handle edge cases, boundary conditions, and invalid inputs",
                "3. **Clear Structure**: Use readable code with appropriate comments for complex operations",
                "4. **Memory Safety**: Implement proper bounds checking and memory access validation",
                "5. **Numerical Stability**: Use appropriate data types and avoid numerical instabilities"
            ]
        else:
            instruction_parts = [
                "**Optimization Instructions:**",
                "1. **Targeted Improvement**: Address specific issues identified in the feedback",
                "2. **Performance Focus**: Apply optimizations while maintaining correctness",
                "3. **Systematic Approach**: Make incremental improvements that build on working elements",
                "4. **Alternative Strategies**: Consider different algorithmic approaches if current approach has limitations",
                "5. **Validation**: Ensure optimizations don't introduce new correctness issues"
            ]
            
        parts.extend(instruction_parts)
        parts.extend([""])
        
        # Language-specific code generation instructions
        code_instructions = {
            "hip": [
                "**HIP Code Requirements:**",
                "- Include all necessary headers: #include <hip/hip_runtime.h>, #include <iostream>, #include <cmath>",
                "- Implement robust error checking for all HIP API calls",
                "- Use appropriate __launch_bounds__ for occupancy optimization",
                "- Optimize for AMD GPU architecture with proper memory coalescing",
                "- Include both kernel function and required C wrapper interface",
                "",
                "Generate ONLY the complete HIP C++ code with no additional explanation or markdown formatting."
            ],
            "cuda": [
                "**CUDA Code Requirements:**",
                "- Include all necessary headers: #include <cuda_runtime.h>, #include <iostream>, #include <cmath>",
                "- Implement comprehensive error checking for all CUDA API calls",
                "- Use appropriate __launch_bounds__ for occupancy optimization",
                "- Optimize for NVIDIA GPU architecture with proper memory coalescing",
                "- Include both kernel function and required C wrapper interface",
                "",
                "Generate ONLY the complete CUDA C++ code with no additional explanation or markdown formatting."
            ],
            "triton": [
                "**Triton Code Requirements:**",
                "- Include all necessary imports: import torch, import triton, import triton.language as tl",
                "- Use appropriate @triton.jit decorators and autotuning configurations",
                "- Implement proper block-level optimizations and memory access patterns",
                "- Follow Triton best practices for GPU kernel optimization",
                "- Include both kernel function and required wrapper function",
                "",
                "Generate ONLY the complete Triton Python code with no additional explanation or markdown formatting."
            ]
        }
        
        parts.extend(code_instructions[self.kernel_lang])
        
        return "\n".join(parts)

    def generate(
        self,
        torch_expl : str,
        doc_context: str,
        feedback   : str = "",
        iter_idx   : int = 0,
        previous_kernel: str = "",
        torch_file: str = "",
        baseline_us: float = None
    ) -> str:
        """
        Enhanced kernel generation with multi-stage optimization and debugging trap prevention.
        
        • iter_idx == 0  → produce a *naïve, correctness-first* kernel
        • iter_idx > 0   → read compile/runtime feedback and optimise
        • iter_idx >= 1  → include previous kernel attempt for context
        
        Additional parameters for enhanced parallel generation compatibility:
        • torch_file: Original PyTorch file path (for context)
        • baseline_us: Baseline performance for optimization targeting
        """
        # ------------------------------------------------------------------
        # 1) Enhanced system prompt template selection
        # ------------------------------------------------------------------
        if iter_idx == 0:
            sys_tpl = Path(f"src/prompts/{self.kernel_lang}_naive.txt").read_text()
        else:
            specs = get_gpu_specs()
            sys_tpl = Path(f"src/prompts/{self.kernel_lang}_opt.txt").read_text().format(
                stage=iter_idx,
                **specs
            )

        # Enhance system prompt with stage-specific guidance
        if iter_idx > 0:
            stage_guidance = self._get_stage_specific_guidance(iter_idx, feedback, baseline_us)
            sys_tpl = f"{sys_tpl}\n\n{stage_guidance}"

        self.system_prompt = {"role": "system", "content": sys_tpl}

        # ------------------------------------------------------------------
        # 2) Build enhanced user message with comprehensive context
        # ------------------------------------------------------------------
        user_content = self.build_user_prompt(torch_expl, doc_context, feedback, previous_kernel, iter_idx)
        messages = [self.system_prompt, {"role": "user", "content": user_content}]

        # ------------------------------------------------------------------
        # 3) Adaptive sampling strategy based on iteration and feedback
        # ------------------------------------------------------------------
        config_key = self.kernel_lang if self.kernel_lang in CFG else "gpu_specs"
        agent_models = CFG.get("openai", {}).get("agent_models", {})
        agent_config = agent_models.get("kernel_generator", {})
        
        # Enhanced temperature and reasoning strategy based on iteration
        if iter_idx == 0:
            # Conservative for initial correctness-focused generation
            temperature = agent_config.get("temperature", 0.1)
            reasoning_effort = "high"
            max_tokens = 8192
        elif iter_idx <= 2:
            # Moderate exploration for early optimization
            temperature = agent_config.get("temperature", 0.2)
            reasoning_effort = "medium"
            max_tokens = 8192
        else:
            # Higher exploration for advanced optimization stages
            temperature = min(agent_config.get("temperature", 0.3) + (iter_idx - 2) * 0.1, 0.6)
            reasoning_effort = "medium"
            max_tokens = 10240

        # ------------------------------------------------------------------
        # 4) Generate kernel with enhanced error handling
        # ------------------------------------------------------------------
        try:
            resp = chat(
                messages=messages,
                max_completion_tokens=max_tokens,
                reasoning_effort=reasoning_effort,
                temperature=temperature,
                agent_name="kernel_generator"
            )
            
            # Enhanced response processing
            try:
                raw = resp.choices[0].message.content
            except (AttributeError, IndexError, TypeError):
                raw = str(resp)
            
            # Clean up response - remove markdown formatting
            kernel_src = self._clean_kernel_response(raw)
            
            # Enhanced usage tracking
            try:
                last_usage = resp.usage
            except AttributeError:
                last_usage = None

        except Exception as e:
            log.append({
                "event": "generation_error",
                "iter": iter_idx,
                "error": str(e),
                "agent": self.role
            })
            # Return a basic fallback kernel structure
            return self._generate_fallback_kernel(torch_expl)

        # ------------------------------------------------------------------
        # 5) Enhanced validation and logging
        # ------------------------------------------------------------------
        validation_result = self._validate_generated_kernel(kernel_src, iter_idx)
        
        log.append({
            "event": self.role,
            "iter": iter_idx,
            "prompt": user_content[:2000] + "..." if len(user_content) > 2000 else user_content,
            "response": kernel_src[:1000] + "..." if len(kernel_src) > 1000 else kernel_src,
            "usage": last_usage,
            "validation": validation_result,
            "temperature": temperature,
            "reasoning_effort": reasoning_effort
        })
        
        return kernel_src

    def _get_stage_specific_guidance(self, iter_idx: int, feedback: str, baseline_us: float = None) -> str:
        """Provide stage-specific optimization guidance"""
        if iter_idx == 1:
            return """
**Stage 1 Focus: Error Resolution and Basic Optimization**
- Priority: Fix compilation errors and runtime failures
- Apply basic optimizations: memory coalescing, proper bounds checking
- Ensure numerical correctness and stability"""
        
        elif iter_idx == 2:
            return """
**Stage 2 Focus: Performance Optimization**
- Priority: Improve memory bandwidth utilization and compute efficiency
- Apply advanced optimizations: shared memory usage, loop unrolling
- Target significant performance improvements while maintaining correctness"""
        
        elif iter_idx >= 3:
            perf_target = f" Target performance improvement over baseline: {baseline_us}us" if baseline_us else ""
            return f"""
**Stage {iter_idx} Focus: Advanced Optimization and Algorithmic Improvements**
- Priority: Explore alternative algorithms and advanced GPU programming techniques
- Consider architectural-specific optimizations and micro-optimizations
- May require fundamental algorithmic changes for breakthrough performance{perf_target}"""
        
        return ""

    def _clean_kernel_response(self, raw_response: str) -> str:
        """Enhanced response cleaning with better format detection"""
        # Remove markdown code blocks
        raw = re.sub(r'^```[^\n]*\n', '', raw_response)
        raw = re.sub(r'\n```$', '', raw)
        
        # Remove language identifiers that might be on their own line
        lines = raw.splitlines()
        if lines and re.fullmatch(r'[A-Za-z0-9_+\-]+', lines[0]):
            lines.pop(0)
        
        # Remove common LLM artifacts
        cleaned_lines = []
        for line in lines:
            # Skip explanation lines that sometimes appear
            if line.strip().startswith("Here") and ("code" in line.lower() or "implementation" in line.lower()):
                continue
            if line.strip().startswith("The") and ("above" in line.lower() or "following" in line.lower()):
                continue
            cleaned_lines.append(line)
        
        return "\n".join(cleaned_lines).strip()

    def _validate_generated_kernel(self, kernel_src: str, iter_idx: int) -> dict:
        """Basic validation of generated kernel structure"""
        validation = {
            "has_kernel_function": False,
            "has_main_function": False,
            "has_wrapper_function": False,
            "includes_headers": False,
            "length_check": len(kernel_src) > 100,
            "issues": []
        }
        
        # Check for required components based on kernel language
        if self.kernel_lang in ["hip", "cuda"]:
            validation["has_kernel_function"] = "__global__" in kernel_src
            validation["has_main_function"] = "int main(" in kernel_src
            validation["has_wrapper_function"] = "extern \"C\" void run_kernel" in kernel_src
            validation["includes_headers"] = "#include" in kernel_src
            
            if not validation["has_kernel_function"]:
                validation["issues"].append("Missing __global__ kernel function")
            if not validation["has_wrapper_function"]:
                validation["issues"].append("Missing required C wrapper function")
                
        elif self.kernel_lang == "triton":
            validation["has_kernel_function"] = "@triton.jit" in kernel_src
            validation["includes_headers"] = "import triton" in kernel_src
            
            if not validation["has_kernel_function"]:
                validation["issues"].append("Missing @triton.jit decorator")
        
        return validation

    def _generate_fallback_kernel(self, torch_expl: str) -> str:
        """Generate a basic fallback kernel when generation fails"""
        if self.kernel_lang == "hip":
            return """#include <hip/hip_runtime.h>
#include <iostream>

__global__ void fallback_kernel(float* input, float* output, int size) {
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx >= size) return;
    output[idx] = input[idx];  // Simple copy operation
}

extern "C" void run_kernel(void* input, void* output, int size) {
    float* d_input = static_cast<float*>(input);
    float* d_output = static_cast<float*>(output);
    int block_size = 256;
    int grid_size = (size + block_size - 1) / block_size;
    fallback_kernel<<<grid_size, block_size>>>(d_input, d_output, size);
    hipDeviceSynchronize();
}

int main() { return 0; }"""
        # Add fallbacks for other languages as needed
        return "// Fallback kernel generation failed"
