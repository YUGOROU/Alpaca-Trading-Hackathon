"""Model selection lives in the environment, not in code (README §5).

Each role (THESIS = the reasoning brain, FAST = cheap high-frequency) reads its provider
+ model + key from `.env`, so you swap DeepSeek <-> OpenAI <-> Groq <-> HF by editing
one file. All providers below expose an OpenAI-compatible endpoint, so one client (see
`model/llm.py`) drives them all.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# OpenAI-compatible base URLs per provider (override with {ROLE}_BASE_URL).
_PROVIDER_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com",
    "groq": "https://api.groq.com/openai/v1",
    "huggingface": "https://router.huggingface.co/v1",
    "cerebras": "https://api.cerebras.ai/v1",
}
# Which env var holds each provider's key (used when {ROLE}_API_KEY is unset).
_PROVIDER_KEYS = {
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "groq": "GROQ_API_KEY",
    "huggingface": "HF_TOKEN",
    "cerebras": "CEREBRAS_API_KEY",
}


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    model: str
    api_key: str | None
    base_url: str
    temperature: float


def load_model_config(role: str = "THESIS") -> ModelConfig:
    """Build a ModelConfig for a role from `{ROLE}_*` env vars (README §5)."""
    role = role.upper()
    provider = os.getenv(f"{role}_PROVIDER", "openai").lower()
    model = os.getenv(f"{role}_MODEL", "gpt-4o-mini")
    temperature = float(os.getenv(f"{role}_TEMPERATURE", "0.2") or 0.2)
    base_url = os.getenv(f"{role}_BASE_URL") or _PROVIDER_BASE_URLS.get(
        provider, _PROVIDER_BASE_URLS["openai"]
    )
    api_key = (
        os.getenv(f"{role}_API_KEY")
        or os.getenv(_PROVIDER_KEYS.get(provider, ""), "")
        or None
    )
    return ModelConfig(provider, model, api_key, base_url, temperature)
