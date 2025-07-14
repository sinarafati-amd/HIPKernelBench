from __future__ import annotations
import os
from typing import Any
import yaml
from langchain_openai import AzureOpenAIEmbeddings 

__all__ = ["get_embedder"]

def _load_cfg(path: str | None = None) -> dict[str, Any]:
    if path is None:
        path = "config.yml"
    try:
        with open(path, "r") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        raise FileNotFoundError(f"Missing config file: {path}")


def get_embedder(cfg_path: str | None = None) -> AzureOpenAIEmbeddings:
    """Return a configured AzureOpenAIEmbeddings instance."""
    root = _load_cfg(cfg_path).get("openai", {})
    url = root.get("base_url") or os.getenv("AZURE_OPENAI_ENDPOINT")
    if not url:
        raise ValueError("Azure endpoint must be set in config or AZURE_OPENAI_ENDPOINT")
    api_key = root.get("api_key") or os.getenv("AZURE_OPENAI_API_KEY")
    if not api_key:
        raise ValueError("Azure key must be set in config or AZURE_OPENAI_API_KEY")

    headers = dict(root.get("headers", {}))
    headers.setdefault("Ocp-Apim-Subscription-Key", api_key)

    kwargs = dict(
        azure_endpoint=url,
        api_key="dummy",           # real auth via header key
        model=root.get("embedding_model", "text-embedding-3-large"),
        api_version=str(root.get("embedding_api_version", "2024-02-01")).strip(),
        default_headers=headers,
    )
    return AzureOpenAIEmbeddings(**kwargs)
