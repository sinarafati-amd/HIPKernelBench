from __future__ import annotations
import os, yaml, openai
from functools import lru_cache
from typing import List, Dict, Any, Optional

# ---------------------------------------------------------------------
# Load connection settings
# ---------------------------------------------------------------------

def _load_cfg() -> dict:
    if os.path.exists("config.yml"):
        return yaml.safe_load(open("config.yml")).get("openai", {})
    return {}

@lru_cache
def _get_client_for_agent(agent_name: Optional[str] = None) -> tuple[openai.AzureOpenAI, str, str]:
    """Get client configured for specific agent or default"""
    cfg = _load_cfg()
    
    # Check for agent-specific configuration
    agent_models = cfg.get("agent_models", {})
    agent_config = agent_models.get(agent_name, {}) if agent_name else {}
    
    # Use agent-specific values or fall back to defaults
    base_url     = os.getenv("AZURE_OPENAI_BASE", cfg.get("base_url"))
    api_key      = os.getenv("AZURE_OPENAI_KEY",  cfg.get("api_key",  "dummy"))
    api_version  = agent_config.get("api_version", cfg.get("api_version",  "2024-12-01-preview"))
    model_id     = agent_config.get("model_name", cfg.get("model_name",  "o3-mini"))
    headers      = cfg.get("headers", {"user": "sirafati"})
    
    client = openai.AzureOpenAI(
        api_key       =  "dummy",
        api_version   = api_version,
        base_url      = base_url,
        default_headers = headers,
    )
    # Azure format:  <base>/openai/deployments/<deployment-id>
    client.base_url = f'{base_url}/openai/deployments/{model_id}'
    return client, model_id, api_version

# Backward compatibility
@lru_cache
def _get_client() -> tuple[openai.AzureOpenAI, str]:
    """Backward compatibility function"""
    client, model_id, _ = _get_client_for_agent()
    return client, model_id


# ---------------------------------------------------------------------
# Public helper 
# ---------------------------------------------------------------------

def chat(
    messages: List[Dict[str, str]],
    temperature: float = 0.2,
    stream: bool = False,
    max_completion_tokens: int = 512,
    reasoning_effort: str = "low",
    agent_name: Optional[str] = None,  
    **extra,
) -> openai.types.chat.ChatCompletion:
    """
    Thin convenience wrapper so every agent looks identical.
    
    Parameters mirror the REST API (only most-used args exposed here).
    
    Args:
        agent_name: Optional agent name to use agent-specific model configuration
    """
    client, model_id, api_version = _get_client_for_agent(agent_name)
    params = {
        "model": model_id,
        "messages": messages,
        "stream": stream,
        "max_completion_tokens": max_completion_tokens,
        "reasoning_effort": reasoning_effort,  # Azure extension
    }
    
    # Only add temperature if model supports it (o3 models might not)
    if "o3" not in model_id.lower():
        params["temperature"] = temperature
    
    # merge any other extra args 
    params.update({k: v for k, v in extra.items() if k not in ["temperature", "agent_name"]})
    return client.chat.completions.create(**params)
