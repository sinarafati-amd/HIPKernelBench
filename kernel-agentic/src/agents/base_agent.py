import yaml, json
from typing import Dict, Any
import os 
import sys 
from pathlib import Path
dir_path=str(Path(os.path.dirname(Path(__file__))).parent)
if dir_path not in sys.path:
    sys.path.append(dir_path)

from utils.llm_client import chat
from utils.logger import log

CFG = yaml.safe_load(open("config.yml"))

class BaseAgent:
    """
    Parent class for every LLM-powered agent.
    Handles prompt assembly, temperature, logging, etc.
    """

    def __init__(self, role: str, system_prompt: str):
        self.role          = role
        self.system_prompt = {"role": "system", "content": system_prompt}
        
        # Get agent-specific configuration
        agent_models = CFG.get("openai", {}).get("agent_models", {})
        self.agent_config = agent_models.get(role, {})

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------
    def _call_llm(self, user_content: str) -> str:
        messages = [self.system_prompt,
                    {"role": "user", "content": user_content}]

        # Use agent-specific temperature or fall back to global config
        temperature = self.agent_config.get("temperature", CFG["openai"]["temperature"])

        resp = chat(
            messages                   = messages,
            temperature                = temperature,
            max_completion_tokens      = 2048,
            reasoning_effort           = "medium",
            agent_name                 = self.role  # Pass agent name for model selection
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
