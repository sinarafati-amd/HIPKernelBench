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
        lang_map = {
            "hip": "HIP",
            "cuda": "CUDA", 
            "triton": "Triton"
        }
        lang_display = lang_map[self.kernel_lang]
        if iter_idx == 0:
            parts = [f"Task: Rewrite the PyTorch code in form of Kernel described below, as an efficient {lang_display} kernel.",
                    "",
                    "[PyTorch explanation]", torch_expl,
                    ""]
            if not doc_ctx.strip() == "":
                if self.kernel_lang in ["hip", "cuda"]:
                    parts.extend([f"[Relevant {lang_display} information]", doc_ctx])
                else:  # triton
                    parts.extend(["[Relevant Triton information]", doc_ctx])
        else:
            if previous_kernel.strip():
                parts = [f"Task: Refine the {lang_display} kernel based on the provided feedback and previous attempts.",
                        "",
                        "[PyTorch explanation]", torch_expl,
                        "",
                        f"[Previous {lang_display} kernel attempt]",
                        "```",
                        previous_kernel.strip(),
                        "```",
                        ""]
            else:
                parts = [f"Task: Refine the {lang_display} kernel based on the provided feedback.",
                        "",
                        "[PyTorch explanation]", torch_expl,
                        ""]
            
        if feedback.strip():
            parts += ["", REFINE_PROMPT.format(error_or_profile=feedback[:6000])]
            
        code_instruction = {
            "hip": "Write the code now. Please only provide the HIP kernel code with nothing extra no markdown ONLY hip code.",
            "cuda": "Write the code now. Please only provide the CUDA kernel code with nothing extra no markdown ONLY cuda code.",
            "triton": "Write the code now. Please only provide the Triton kernel code with nothing extra no markdown ONLY triton code."
        }
        
        parts += [code_instruction[self.kernel_lang]]
        return "\n".join(parts)

    def generate(
        self,
        torch_expl : str,
        doc_context: str,
        feedback   : str = "",
        iter_idx   : int = 0,
        previous_kernel: str = "",
    ) -> str:
        """
        • iter_idx == 0  → produce a *naïve, correctness-first* kernel
        • iter_idx > 0   → read compile/runtime feedback and optimise
        • iter_idx >= 1  → include previous kernel attempt for context
        """
        # ------------------------------------------------------------------
        # 1) Choose the appropriate system-prompt template
        # ------------------------------------------------------------------
        if iter_idx == 0:
            sys_tpl = Path(f"src/prompts/{self.kernel_lang}_naive.txt").read_text()
        else:
            specs  = get_gpu_specs()                              # auto-query ROCm/CUDA
            sys_tpl = Path(f"src/prompts/{self.kernel_lang}_opt.txt").read_text().format(
                stage    = iter_idx,
                **specs
            )

        # overwrite the system message for this call
        self.system_prompt = {"role": "system", "content": sys_tpl}

        # ------------------------------------------------------------------
        # 2) Build the user message (torch-explanation + RAG chunks + feedback + previous kernel)
        # ------------------------------------------------------------------
        user_content = self.build_user_prompt(torch_expl, doc_context, feedback, previous_kernel, iter_idx)
        messages     = [self.system_prompt, {"role": "user", "content": user_content}]

        # ------------------------------------------------------------------
        # 3) Sample one candidate for the naïve pass, best-of-N thereafter
        # ------------------------------------------------------------------
        config_key = self.kernel_lang if self.kernel_lang in CFG else "hip"  # fallback to hip config
        candidates, last_usage = [], None

        # Get agent-specific temperature or use config default
        agent_models = CFG.get("openai", {}).get("agent_models", {})
        agent_config = agent_models.get("kernel_generator", {})
        temperature = agent_config.get("temperature", CFG.get("openai", {}).get("temperature", 0.2))

        resp     = chat(
            messages               = messages,
            max_completion_tokens  = 8192,
            reasoning_effort       = "high" if iter_idx == 0 else "medium",
            temperature            = temperature,
            agent_name             = "kernel_generator"  # Pass agent name for model selection
        )
        # Handle response content safely for both OpenAI and Claude
        try:
            raw = resp.choices[0].message.content
        except (AttributeError, IndexError, TypeError):
            # Fallback for unexpected response format
            raw = str(resp)
            
        raw = re.sub(r'^```[^\n]*\n', '', raw)
        raw = re.sub(r'\n```$', '', raw)

        lines = raw.splitlines()
        if lines and re.fullmatch(r'[A-Za-z0-9_+\-]+', lines[0]):
            lines.pop(0)
        kernel_src = "\n".join(lines).strip()
        
        # Handle usage information safely
        try:
            last_usage = resp.usage
        except AttributeError:
            last_usage = None


        # ------------------------------------------------------------------
        # 5) Log and return
        # ------------------------------------------------------------------
        log.append({
            "event"   : self.role,
            "iter"    : iter_idx,
            "prompt"  : user_content,
            "response": kernel_src,
            "usage"   : last_usage,
        })
        return kernel_src
