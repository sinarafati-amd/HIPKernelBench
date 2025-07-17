from .base_agent import BaseAgent
from ..utils.llm_client import chat
from ..utils.gpu_specs import get_gpu_specs
from ..utils.logger import log
import yaml
import os
from pathlib import Path
import re

CFG = yaml.safe_load(open("config.yml"))

# Prompt templates loader
def _load_prompt(filename: str) -> str:
    path = os.path.join(os.path.dirname(__file__), os.pardir, "prompts", filename)
    with open(path, 'r') as fp:
        return fp.read()

# Optimization template (language agnostic)
OPTIMIZE_PROMPT = _load_prompt("optimization.txt") if os.path.exists(os.path.join(os.path.dirname(__file__), os.pardir, "prompts", "optimization.txt")) else ""


class KernelOptimizer(BaseAgent):
    """
    Specialized agent for optimizing existing kernel code.
    """
    def __init__(self, kernel_lang: str = None):
        if kernel_lang is None:
            kernel_lang = CFG.get("Pipeline", {}).get("kernel_lang", "hip").lower()
        
        self.kernel_lang = kernel_lang.lower()
        if self.kernel_lang not in ["hip", "cuda", "triton"]:
            raise ValueError(f"Unsupported kernel language: {self.kernel_lang}")

        specs = get_gpu_specs()
        super().__init__(f"{self.kernel_lang}_optimizer", "<will-be-replaced>")

    def build_optimization_prompt(self, kernel_code: str, analysis: str, doc_ctx: str, feedback: str, iter_idx: int = 0) -> str:
        """
        Build prompt for kernel optimization.
        """
        lang_map = {
            "hip": "HIP",
            "cuda": "CUDA", 
            "triton": "Triton"
        }
        lang_display = lang_map[self.kernel_lang]
        
        parts = [
            f"Task: Optimize the given {lang_display} kernel for better performance while maintaining correctness.",
            "",
            "[Kernel Analysis]",
            analysis,
            "",
            f"[Current {lang_display} Kernel]",
            "```",
            kernel_code.strip(),
            "```",
            ""
        ]
        
        if not doc_ctx.strip() == "":
            parts.extend([f"[Relevant {lang_display} optimization information]", doc_ctx, ""])
            
        if feedback.strip():
            parts += ["[Performance Feedback & Profile Data]", feedback[:6000], ""]
            
        optimization_instructions = {
            "hip": """
                    Optimize this HIP kernel focusing on:
                    - Memory coalescing and bank conflicts
                    - Warp/wavefront utilization  
                    - Register usage optimization
                    - Shared memory optimization
                    - Loop unrolling and vectorization
                    - ROCm-specific optimizations

                    Write the optimized code now. Please only provide the HIP kernel code with nothing extra no markdown ONLY hip code.""",
                                "cuda": """
                    Optimize this CUDA kernel focusing on:
                    - Memory coalescing and bank conflicts
                    - Warp utilization and occupancy
                    - Register usage optimization  
                    - Shared memory optimization
                    - Loop unrolling and vectorization
                    - Tensor core utilization (if applicable)

                    Write the optimized code now. Please only provide the CUDA kernel code with nothing extra no markdown ONLY cuda code.""",
                                "triton": """
                    Optimize this Triton kernel focusing on:
                    - Block size and tiling strategies
                    - Memory hierarchy utilization
                    - Vectorization opportunities
                    - Fusion opportunities
                    - Auto-tuning parameter selection

                    Write the optimized code now. Please only provide the Triton kernel code with nothing extra no markdown ONLY triton code."""
        }
        
        parts.append(optimization_instructions[self.kernel_lang])
        return "\n".join(parts)

    def optimize(
        self,
        kernel_code: str,
        analysis: str,
        doc_context: str = "",
        feedback: str = "",
        iter_idx: int = 0,
    ) -> str:
        """
        Optimize an existing kernel based on analysis and feedback.
        """
        # Choose the appropriate system-prompt template for optimization
        if iter_idx == 0:
            sys_tpl = Path(f"src/prompts/{self.kernel_lang}_optimize.txt").read_text() if Path(f"src/prompts/{self.kernel_lang}_optimize.txt").exists() else Path(f"src/prompts/{self.kernel_lang}_opt.txt").read_text()
        else:
            specs = get_gpu_specs()
            sys_tpl = Path(f"src/prompts/{self.kernel_lang}_opt.txt").read_text().format(
                stage=iter_idx,
                **specs
            )

        # Overwrite the system message for this call
        self.system_prompt = {"role": "system", "content": sys_tpl}

        # Build the user message
        user_content = self.build_optimization_prompt(kernel_code, analysis, doc_context, feedback, iter_idx)
        messages = [self.system_prompt, {"role": "user", "content": user_content}]

        # Get agent-specific temperature or use config default
        agent_models = CFG.get("openai", {}).get("agent_models", {})
        agent_config = agent_models.get("kernel_generator", {})  # Reuse kernel_generator config
        temperature = agent_config.get("temperature", CFG.get("openai", {}).get("temperature", 0.2))

        resp = chat(
            messages=messages,
            max_completion_tokens=8192,
            reasoning_effort="high" if iter_idx == 0 else "medium",
            temperature=temperature,
            agent_name="kernel_generator"  # Reuse kernel_generator agent config
        )
        
        raw = resp.choices[0].message.content
        raw = re.sub(r'^```[^\n]*\n', '', raw)
        raw = re.sub(r'\n```$', '', raw)

        lines = raw.splitlines()
        if lines and re.fullmatch(r'[A-Za-z0-9_+\-]+', lines[0]):
            lines.pop(0)
        kernel_src = "\n".join(lines).strip()
        last_usage = resp.usage

        # Log the optimization attempt
        log.append({
            "event": f"{self.role}_optimization",
            "iter": iter_idx,
            "prompt": user_content,
            "response": kernel_src,
            "usage": last_usage,
        })
        
        return kernel_src
