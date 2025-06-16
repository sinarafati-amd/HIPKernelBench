from .base_agent import BaseAgent
from ..utils.llm_client import chat
from ..utils.gpu_specs import get_gpu_specs
from ..utils.logger import log
import yaml, random
import os 
from pathlib import Path
CFG = yaml.safe_load(open("config.yml"))


# Prompt templates loader
def _load_prompt(filename: str) -> str:
    path = os.path.join(os.path.dirname(__file__), os.pardir, "prompts", filename)
    with open(path, 'r') as fp:
        return fp.read()

# # Base and refinement templates
# BASE_PROMPT = _load_prompt("hip_base.txt")
REFINE_PROMPT = _load_prompt("refinement.txt")


class HIPGenerator(BaseAgent):
    """
    HIPGenerator uses structured prompts with GPU specs and best-of-N sampling.
    """
    def __init__(self):
        specs = get_gpu_specs()
        # sys_prompt = BASE_PROMPT.format(**specs)
        super().__init__("hip_generator", "<will-be-replaced>")

    def _build_user_prompt(self, torch_expl: str, doc_ctx: str, feedback: str) -> str:
        parts = ["<thinking>",
                "Task: Rewrite the PyTorch operation described below as an efficient HIP kernel.",
                "",
                "[PyTorch explanation]", torch_expl,
                "",
                "[Relevant HIP documentation]", doc_ctx]
        if feedback:
            parts += ["", REFINE_PROMPT.format(error_or_profile=feedback[:4000])]
        parts += ["Write the code now. Please only provide the HIP kernel code with nothing extra no markdown ONLY hip code.",
                "</thinking>"]
        return "\n".join(parts)

    def generate(
        self,
        torch_expl : str,
        doc_context: str,
        feedback   : str = "",
        iter_idx   : int = 0,
    ) -> str:
        """
        • iter_idx == 0  → produce a *naïve, correctness-first* HIP kernel
        • iter_idx > 0   → read compile/runtime feedback and optimise
        """
        # ------------------------------------------------------------------
        # 1) Choose the appropriate system-prompt template
        # ------------------------------------------------------------------
        if iter_idx == 0:
            sys_tpl = Path("src/prompts/hip_naive.txt").read_text()
        else:
            specs  = get_gpu_specs()                              # auto-query ROCm
            sys_tpl = Path("src/prompts/hip_opt.txt").read_text().format(
                stage    = iter_idx,
                **specs
            )

        # overwrite the system message for this call
        self.system_prompt = {"role": "system", "content": sys_tpl}

        # ------------------------------------------------------------------
        # 2) Build the user message (torch-explanation + RAG chunks + feedback)
        # ------------------------------------------------------------------
        user_content = self._build_user_prompt(torch_expl, doc_context, feedback)
        messages     = [self.system_prompt, {"role": "user", "content": user_content}]

        # ------------------------------------------------------------------
        # 3) Sample one candidate for the naïve pass, best-of-N thereafter
        # ------------------------------------------------------------------
        k = 1 if iter_idx == 0 else CFG.get("hip", {}).get("n_samples", 4)
        candidates, last_usage = [], None

        for _ in range(k):
            resp     = chat(
                messages               = messages,
                max_completion_tokens  = 4096,
                reasoning_effort       = "low" if iter_idx == 0 else "medium",
                temperature            = CFG.get("openai", {}).get("temperature", 0.01),
            )
            hip_src  = resp.choices[0].message.content
            if hip_src.startswith("```"):
                hip_src = hip_src.split("```")[1].replace("```", "").strip()

            candidates.append(hip_src)
            last_usage = resp.usage

        # ------------------------------------------------------------------
        # 4) Pick a candidate  (random or later: latency model)
        # ------------------------------------------------------------------
        hip_src = random.choice(candidates)

        # ------------------------------------------------------------------
        # 5) Log and return
        # ------------------------------------------------------------------
        log.append({
            "event"   : self.role,
            "iter"    : iter_idx,
            "prompt"  : user_content,
            "response": hip_src,
            "usage"   : last_usage,
        })
        return hip_src
