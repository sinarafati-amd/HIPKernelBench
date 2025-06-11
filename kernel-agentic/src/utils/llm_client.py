"""
Centralised Azure OpenAI chat wrapper
====================================

All agents call `llm_client.chat()` instead of talking to the
OpenAI SDK directly.  Connection parameters are taken from **env vars
_or_ config.yml** so you can point the codebase at any Azure
deployment without touching the agents.
"""

from __future__ import annotations
import os, yaml, openai
from functools import lru_cache
from typing import List, Dict, Any

# ---------------------------------------------------------------------
# Load connection settings
# ---------------------------------------------------------------------

def _load_cfg() -> dict:
    if os.path.exists("config.yml"):
        return yaml.safe_load(open("config.yml")).get("openai", {})
    return {}

@lru_cache
def _get_client() -> tuple[openai.AzureOpenAI, str]:
    cfg = _load_cfg()

    base_url     = os.getenv("AZURE_OPENAI_BASE", cfg.get("base_url", "my-base-url"))
    api_key      = os.getenv("AZURE_OPENAI_KEY",  cfg.get("api_key",  "dummy"))
    api_version  = cfg.get("api_version",  "2024-12-01-preview")
    model_id     = cfg.get("model_name",  "o3-mini")
    headers      = cfg.get("headers", {"user": "sirafati"})
    client = openai.AzureOpenAI(
        api_key       =  "dummy",
        api_version   = api_version,
        base_url      = base_url,
        default_headers = headers,
    )
    # Azure format:  <base>/openai/deployments/<deployment-id>
    client.base_url = f'{base_url}/openai/deployments/{model_id}'
    return client, model_id


# ---------------------------------------------------------------------
# Public helper 
# ---------------------------------------------------------------------

def chat(
    messages: List[Dict[str, str]],
    temperature: float = 1,
    stream: bool = False,
    max_completion_tokens: int = 512,
    reasoning_effort: str = "low",
    **extra,
) -> openai.types.chat.ChatCompletion:
    """
    Thin convenience wrapper so every agent looks identical.

    Parameters mirror the REST API (only most-used args exposed here).
    """
    client, model_id = _get_client()
    
    
    params = {
        "model": model_id,
        "messages": messages,
        "stream": stream,
        "max_completion_tokens": max_completion_tokens,
        "reasoning_effort": reasoning_effort,  # Azure extension
    }
    # merge any other extra args (but omit temperature)
    params.update({k: v for k, v in extra.items() if k != "temperature"})
    return client.chat.completions.create(**params)

    # return client.chat.completions.create(
    #     model                    = model_id,
    #     messages                 = messages,
    #     temperature              = temperature,
    #     stream                   = stream,
    #     max_completion_tokens    = max_completion_tokens,
    #     reasoning_effort         = reasoning_effort,  
    # )
