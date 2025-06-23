import os
import yaml
from langchain_openai import AzureOpenAIEmbeddings 

def get_embedder(config_path: str = "config.yml") -> AzureOpenAIEmbeddings:
    """
    Load Azure OpenAI embedding settings from a YAML file (with env‐var fallbacks),
    validate them, and return an AzureOpenAIEmbeddings instance.
    """
    # Load config
    try:
        with open(config_path, "r") as f:
            cfg = yaml.safe_load(f) or {}
    except FileNotFoundError:
        raise FileNotFoundError(f"Config file not found: {config_path}")
    openai_cfg = cfg.get("openai", {})

    # Endpoint and API version (fallback to ENV)
    url = openai_cfg.get("base_url") or os.getenv("AZURE_OPENAI_ENDPOINT")
    if not url:
        raise ValueError("Azure OpenAI endpoint must be set in config or AZURE_OPENAI_ENDPOINT")
    api_version = (
        openai_cfg.get("embedding_api_version")
        or os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01")
    )
    api_version=str(api_version).strip()
    # API key (fallback to ENV)
    api_key = openai_cfg.get("api_key") or os.getenv("AZURE_OPENAI_API_KEY")
    if not api_key:
        raise ValueError("Azure OpenAI API key must be set in config or AZURE_OPENAI_API_KEY")

    # Embedding model name
    model_name = openai_cfg.get("embedding_model", "text-embedding-3-large") 

    # Optional max_tokens (note: most embedding models ignore this)
    max_tokens = openai_cfg.get("max_tokens")

    # Build headers, ensuring subscription key is present
    headers = dict(openai_cfg.get("headers", {}))
    if "Ocp-Apim-Subscription-Key" not in headers:
        headers["Ocp-Apim-Subscription-Key"] = api_key
    
    # Prepare init kwargs
    init_kwargs = {
        "azure_endpoint": url,
        "api_key": 'dummy',
        "model": model_name,
        "api_version": api_version,
        "default_headers": headers,
    }
    return AzureOpenAIEmbeddings(**init_kwargs)
