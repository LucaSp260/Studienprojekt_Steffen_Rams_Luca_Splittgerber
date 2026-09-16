"""Zentrale Modelle und lokale Konfiguration, keine Schlüssel in SQLite."""

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values, set_key

ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
DEFAULT_MODELS = {"openai": "gpt-5.6-luna", "gemini": "gemini-3.8-flash"}
PROVIDER_LABELS = {"openai": "OpenAI", "gemini": "Google Gemini"}
DEFAULT_EMBEDDING_MODELS = {"openai": "text-embedding-3-small", "gemini": "gemini-embedding-2"}
EMBEDDING_DIMENSIONS = 768


@dataclass
class LLMConfig:
    provider: str
    model: str
    api_key: str = field(repr=False)


@dataclass
class EmbeddingConfig:
    provider: str
    model: str
    api_key: str = field(repr=False)
    dimensions: int = EMBEDDING_DIMENSIONS


def load_embedding_config(provider=None):
    llm = load_config(provider)
    values = dotenv_values(ENV_PATH, interpolate=False) if ENV_PATH.exists() else {}
    name = f"{llm.provider.upper()}_EMBEDDING_MODEL"
    model = values.get(name) or os.getenv(name) or DEFAULT_EMBEDDING_MODELS[llm.provider]
    return EmbeddingConfig(llm.provider, model.strip(), llm.api_key)


def load_config(provider=None):
    values = dotenv_values(ENV_PATH, interpolate=False) if ENV_PATH.exists() else {}
    provider = provider or values.get("LLM_PROVIDER") or os.getenv("LLM_PROVIDER", "openai")
    if provider not in DEFAULT_MODELS:
        raise ValueError("Unbekannter KI-Anbieter. Bitte OpenAI oder Google Gemini auswählen.")
    prefix = provider.upper()
    model = values.get(f"{prefix}_MODEL") or os.getenv(f"{prefix}_MODEL") or DEFAULT_MODELS[provider]
    api_key = values.get(f"{prefix}_API_KEY") or os.getenv(f"{prefix}_API_KEY", "")
    return LLMConfig(provider, model.strip(), api_key.strip())


def save_config(provider, model, api_key="", embedding_model=None):
    previous = load_config(provider)
    model = model.strip()
    api_key = api_key.strip() or previous.api_key
    embedding_model = load_embedding_config(provider).model if embedding_model is None else embedding_model.strip()
    if not model or not embedding_model or any(char in model + api_key + embedding_model for char in "\r\n\x00"):
        raise ValueError("Bitte einen gültigen Modellnamen und einen einzeiligen API-Key angeben.")
    if not api_key:
        raise ValueError("Kein API-Key vorhanden. Bitte zuerst einen Schlüssel eingeben.")
    # Erst vollständig schreiben, dann ersetzen: eine unterbrochene Speicherung
    # darf die bestehende Konfiguration nicht beschädigen.
    descriptor, temporary_name = tempfile.mkstemp(prefix=".env.", dir=ENV_PATH.parent)
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        if ENV_PATH.exists():
            temporary_path.write_text(ENV_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        for name, value in {"LLM_PROVIDER": provider, f"{provider.upper()}_MODEL": model,
                            f"{provider.upper()}_API_KEY": api_key,
                            f"{provider.upper()}_EMBEDDING_MODEL": embedding_model}.items():
            set_key(temporary_path, name, value)
        os.replace(temporary_path, ENV_PATH)
    finally:
        temporary_path.unlink(missing_ok=True)
