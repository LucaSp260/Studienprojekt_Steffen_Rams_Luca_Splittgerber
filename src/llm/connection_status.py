"""Cached model-metadata check: no generation, embeddings or token estimates."""
import hashlib
import streamlit as st
import openai
from google import genai
from google.genai import types

def connection_status(config):
    if not config.api_key or not config.model:
        return False, "API-Key oder Modell fehlt."
    fingerprint = hashlib.sha256(config.api_key.encode()).hexdigest()
    return _cached_status(config.provider, config.model, fingerprint, _api_key=config.api_key)

@st.cache_data(ttl=300, max_entries=32, show_spinner=False)
def _cached_status(provider, model, fingerprint, _api_key):
    del fingerprint
    try:
        if provider == "openai":
            with openai.OpenAI(api_key=_api_key, timeout=8, max_retries=0) as client:
                client.models.retrieve(model)
        elif provider == "gemini":
            with genai.Client(api_key=_api_key, http_options=types.HttpOptions(
                    timeout=8000, retry_options=types.HttpRetryOptions(attempts=0))) as client:
                client.models.get(model=model)
        else:
            return False, "Unbekannter Anbieter."
        return True, "Modellmetadaten erfolgreich abgerufen."
    except Exception:
        # Never expose raw provider responses, credentials or request headers.
        return False, "Anbieter oder Modell nicht erreichbar, oder Zugriff verweigert."
