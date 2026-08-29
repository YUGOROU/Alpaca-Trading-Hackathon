"""One OpenAI-compatible chat client for every provider (README §5).

`langchain-openai` is imported lazily so importing this module never requires it. The
provider/model/key/base_url all come from `model/config.py` (i.e. from `.env`).
"""
from __future__ import annotations

from .config import ModelConfig, load_model_config


def get_chat_model(config: ModelConfig | None = None, *, role: str = "THESIS"):
    """Construct a ChatOpenAI pointed at the configured provider's endpoint."""
    from langchain_openai import ChatOpenAI  # lazy: only needed for real calls

    cfg = config or load_model_config(role)
    if not cfg.api_key:
        raise RuntimeError(
            f"No API key for role {role} (provider {cfg.provider}); set the key in .env."
        )
    return ChatOpenAI(
        model=cfg.model,
        api_key=cfg.api_key,
        base_url=cfg.base_url,
        temperature=cfg.temperature,
    )


def complete(system: str, user: str, *, role: str = "THESIS", model=None) -> str:
    """Send a system+user prompt and return the model's text reply."""
    from langchain_core.messages import HumanMessage, SystemMessage

    model = model or get_chat_model(role=role)
    resp = model.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    return resp.content if hasattr(resp, "content") else str(resp)
