from __future__ import annotations
import os, yaml, openai
from functools import lru_cache
from typing import List, Dict, Any, Optional
import httpx
import time

# ---------------------------------------------------------------------
# Load connection settings
# ---------------------------------------------------------------------

def _load_cfg() -> dict:
    if os.path.exists("config.yml"):
        return yaml.safe_load(open("config.yml")).get("openai", {})
    return {}

class ClaudeResponse:
    """Wrapper to make Claude response compatible with OpenAI format"""
    def __init__(self, claude_response: dict):
        self.choices = [ClaudeChoice(claude_response)]
        self.usage = None  # I am trying to mimic AMD's openai output 
        self._raw_response = claude_response  
    
    def __str__(self):
        """Make the response printable for debugging"""
        if self.choices and hasattr(self.choices[0], 'message'):
            return f"ClaudeResponse(content='{self.choices[0].message.content[:100]}...')"
        return f"ClaudeResponse(raw_keys={list(self._raw_response.keys())})"

class ClaudeChoice:
    """Wrapper for Claude choice to match OpenAI format"""
    def __init__(self, claude_response: dict):
        self.message = ClaudeMessage(claude_response)

class ClaudeMessage:
    """Wrapper for Claude message to match OpenAI format"""
    def __init__(self, claude_response: dict):
        content = ""
        
        #Standard Claude format with content array
        if 'content' in claude_response and isinstance(claude_response['content'], list):
            if claude_response['content'] and 'text' in claude_response['content'][0]:
                content = claude_response['content'][0]['text']
        
        #Direct content field (string)
        elif 'content' in claude_response and isinstance(claude_response['content'], str):
            content = claude_response['content']
        
        #Message nested structure
        elif 'message' in claude_response and 'content' in claude_response['message']:
            message_content = claude_response['message']['content']
            if isinstance(message_content, list) and message_content:
                content = message_content[0].get('text', '')
            elif isinstance(message_content, str):
                content = message_content
        
        #Choices array like OpenAI
        elif 'choices' in claude_response and claude_response['choices']:
            choice = claude_response['choices'][0]
            if 'message' in choice and 'content' in choice['message']:
                content = choice['message']['content']
            elif 'text' in choice:
                content = choice['text']
        
        # Direct text field
        elif 'text' in claude_response:
            content = claude_response['text']
        
        #Response field containing the actual response
        elif 'response' in claude_response:
            response_data = claude_response['response']
            if isinstance(response_data, dict) and 'content' in response_data:
                if isinstance(response_data['content'], list) and response_data['content']:
                    content = response_data['content'][0].get('text', '')
                elif isinstance(response_data['content'], str):
                    content = response_data['content']
        
        self.content = content

        if not content:
            print(f"Warning: Could not extract content from Claude response. Response keys: {list(claude_response.keys())}")
            print(f"Full response: {claude_response}")

def _create_claude_client(base_url: str, api_key: str, username: str) -> callable:
    """Create Claude client function for gateway"""
    def claude_chat_completion(**kwargs) -> ClaudeResponse:
        model = kwargs.get("model")
        url = f"{base_url}/AnthropicVertex/deployments/{model}/chat/completions"
        
        headers = {
            "Ocp-Apim-Subscription-Key": api_key,
            "user": username,
            "Content-Type": "application/json"
        }
        
        data = {
            "messages": kwargs.get("messages", []),
            "max_tokens": kwargs.get("max_completion_tokens", 1024),
            "temperature": kwargs.get("temperature", 0.7),
            "top_p": kwargs.get("top_p", 1),
            "stop": kwargs.get("stop", None)
        }
        

        cfg = _load_cfg()
        claude_config = cfg.get("claude", {})
        timeout_seconds = claude_config.get("timeout", 120)  
        max_retries = claude_config.get("max_retries", 3)
        retry_delay = claude_config.get("retry_delay", 1)
        
        # Configure timeout
        timeout = httpx.Timeout(timeout_seconds, connect=10.0)
        
        last_error = None
        for attempt in range(max_retries):
            try:
                with httpx.Client(timeout=timeout) as client:
                    response = client.post(url, headers=headers, json=data)
                    response.raise_for_status()
                    response_json = response.json()
                    
                    return ClaudeResponse(response_json)
                    
            except httpx.ReadTimeout as e:
                last_error = f"Claude API request timed out after {timeout_seconds} seconds: {e}"
                if attempt < max_retries - 1:
                    print(f"Attempt {attempt + 1} failed with timeout, retrying in {retry_delay}s...")
                    time.sleep(retry_delay)
                    retry_delay *= 2  
                
            except httpx.HTTPStatusError as e:
                last_error = f"Claude API returned error {e.response.status_code}: {e.response.text}"
                if e.response.status_code >= 500 and attempt < max_retries - 1:
                    # Retry on server errors
                    print(f"Server error {e.response.status_code}, retrying in {retry_delay}s...")
                    time.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    break
                    
            except Exception as e:
                last_error = f"Claude API request failed: {e}"
                if attempt < max_retries - 1:
                    print(f"Request failed, retrying in {retry_delay}s...")
                    time.sleep(retry_delay)
                    retry_delay *= 2
        
        # All retries failed
        raise RuntimeError(f"Claude API failed after {max_retries} attempts. Last error: {last_error}")
    
    return claude_chat_completion

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
    model_name     = agent_config.get("model_name", cfg.get("model_name",  "o3-mini"))
    headers      = cfg.get("headers", {"user": "sirafati"})
    
    client = openai.AzureOpenAI(
        api_key       =  "dummy",
        api_version   = api_version,
        base_url      = base_url,
        default_headers = headers,
    )
    # Azure format:  <base>/openai/deployments/<deployment-id>
    client.base_url = f'{base_url}/openai/deployments/{model_name}'
    return client, model_name, api_version

@lru_cache 
def _get_claude_client_for_agent(agent_name: Optional[str] = None) -> tuple[callable, str]:
    """Get Claude client configured for specific agent"""
    cfg = _load_cfg()
    
    # Check for agent-specific configuration
    agent_models = cfg.get("agent_models", {})
    agent_config = agent_models.get(agent_name, {}) if agent_name else {}
    
    # Use agent-specific values or fall back to defaults
    base_url = os.getenv("AZURE_OPENAI_BASE", cfg.get("base_url"))
    api_key = cfg.get("headers", {}).get("Ocp-Apim-Subscription-Key", "dummy")
    username = cfg.get("headers", {}).get("user", "sirafati")
    model_name = agent_config.get("model_name", cfg.get("model_name", "Claude-4"))
    
    claude_client = _create_claude_client(base_url, api_key, username)
    return claude_client, model_name

def _is_claude_model(model_name: str) -> bool:
    """Check if model name indicates Claude model"""
    return "claude" in model_name.lower()

# Backward compatibility
@lru_cache
def _get_client() -> tuple[openai.AzureOpenAI, str]:
    """Backward compatibility function"""
    client, model_name, _ = _get_client_for_agent()
    return client, model_name


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
    # Determine if we should use Claude or OpenAI based on agent configuration
    cfg = _load_cfg()

    public_api_key = cfg.get("public_api_key", "")
    local_llm_enabled = cfg.get("local_llm_enabled", False)
    if local_llm_enabled or public_api_key:
        return chat_without_AMD_gateway(messages, temperature, stream, max_completion_tokens, reasoning_effort, agent_name, **extra)

    agent_models = cfg.get("agent_models", {})
    agent_config = agent_models.get(agent_name, {}) if agent_name else {}
    model_name = agent_config.get("model_name", cfg.get("model_name", ""))
    
    if _is_claude_model(model_name):
        # Use Claude client
        claude_client, model_name = _get_claude_client_for_agent(agent_name)
        
        params = {
            "model": model_name,
            "messages": messages,
            "max_completion_tokens": max_completion_tokens,
            "temperature": temperature,
        }
        
        # merge any other extra args 
        params.update({k: v for k, v in extra.items() if k not in ["agent_name", "reasoning_effort", "stream"]})

        return claude_client(**params)
    else:
        # Use existing OpenAI client (unchanged behavior)
        client, model_name, api_version = _get_client_for_agent(agent_name)
        params = {
            "model": model_name,
            "messages": messages,
            "stream": stream,
            "max_completion_tokens": max_completion_tokens,
            "reasoning_effort": reasoning_effort,  # Azure extension
        }
        
        # Only add temperature if model supports it (o3 models might not)
        if "o3" not in model_name.lower():
            params["temperature"] = temperature
        
        # merge any other extra args 
        params.update({k: v for k, v in extra.items() if k not in ["temperature", "agent_name"]})
        return client.chat.completions.create(**params)


def chat_without_AMD_gateway(
    messages: list[dict[str, str]],
    temperature: float = 0.2,
    stream: bool = False,
    max_completion_tokens: int = 512,
    reasoning_effort: str = "low",
    agent_name: str = None,
    **extra,
) -> "openai.types.chat.ChatCompletion":
    """
    Similar to chat(), but uses the public OpenAI API (public_api_key from config.yml). 
    Please use it cautiously. Do not use it for sensitive data.
    """

    cfg = _load_cfg()

    public_api_key = cfg.get("public_api_key", "")
    local_llm_enabled = cfg.get("local_llm_enabled", False)
    local_llm_base_url = cfg.get("local_llm_base_url", "")

    if local_llm_enabled:
        client = openai.OpenAI(api_key="dummy", base_url=local_llm_base_url)
        model_name = "llamas_team_local_llm"

    else:
        client = openai.OpenAI(api_key=public_api_key)
        model_name = extra.get("model", "o3")  # use o3 model by default
        
    params = {
        "model": model_name,
        "messages": messages,
        "stream": stream,
    }
    # Use correct max tokens parameter name
    if "o3" in model_name.lower():
        params["max_completion_tokens"] = max_completion_tokens
        # Do NOT set temperature for o3 models (only default 1 is supported)
    else:
        params["max_tokens"] = max_completion_tokens
        params["temperature"] = temperature
    # merge any other extra args except temperature and agent_name
    params.update({k: v for k, v in extra.items() if k not in ["temperature", "agent_name"]})
    return client.chat.completions.create(**params)


if __name__ == "__main__":
    
    # Minimal test for public_chat
    test_messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "What is the capital of France?"},
    ]
    try:
        response = chat_without_AMD_gateway(test_messages, temperature=0.1)
        print("public_chat test response:")
        # Try to print the content of the first choice if available
        if hasattr(response, "choices") and response.choices:
            print(response.choices[0].message.content)
        else:
            print(response)
    except Exception as e:
        print(f"public_chat test failed: {e}")


