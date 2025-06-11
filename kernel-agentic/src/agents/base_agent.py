import yaml, json
from typing import Dict, Any
from ..utils.llm_client import chat
from ..utils.logger import log

CFG = yaml.safe_load(open("config.yml"))

class BaseAgent:
    """
    Parent class for every LLM-powered agent.
    Handles prompt assembly, temperature, logging, etc.
    """

    def __init__(self, role: str, system_prompt: str):
        self.role          = role
        self.system_prompt = {"role": "system", "content": system_prompt}

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------
    def _call_llm(self, user_content: str) -> str:
        messages = [self.system_prompt,
                    {"role": "user", "content": user_content}]

        resp = chat(
            messages                   = messages,
            temperature                = CFG["openai"]["temperature"],
            max_completion_tokens      = 2048,
            reasoning_effort           = "high"
        )
        answer = resp.choices[0].message.content
        usage = resp.usage.to_dict() if hasattr(resp.usage, "to_dict") else dict(resp.usage)
        log.append({
            "event"   : self.role,
            "prompt"  : user_content,
            "response": answer,
            "usage"   : usage
        })
        return answer

    # ------------------------------------------------------------------
    # Public facade for children
    # ------------------------------------------------------------------
    def ask(self, prompt: str) -> str:
        return self._call_llm(prompt)
