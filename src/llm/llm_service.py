"""Einziger LLM-Zugang für die Knowledge-Base-Logik."""

from pydantic import BaseModel

from src.llm.base_provider import LLMError
from src.llm.config import load_config
from src.llm.openai_provider import OpenAIProvider
from src.llm.gemini_provider import GeminiProvider
from src.llm.usage import record_service_usage


class ConnectionResult(BaseModel):
    ok: bool


class LLMService:
    def __init__(self, config=None):
        config = config or load_config()
        if not config.api_key.strip():
            raise LLMError("Kein API-Key konfiguriert. Bitte unter Einstellungen einen Schlüssel speichern.")
        if not config.model.strip():
            raise LLMError("Bitte unter Einstellungen einen Modellnamen angeben.")
        providers = {"openai": OpenAIProvider, "gemini": GeminiProvider}
        if config.provider not in providers:
            raise LLMError("Unbekannter KI-Anbieter.")
        self.config = config
        self.provider = providers[config.provider](config)

    def generate(self, prompt, response_model, images=None):
        if images:
            result = self.provider.generate(prompt, response_model, images=images)
        else:
            result = self.provider.generate(prompt, response_model)
        record_service_usage(self.config, getattr(self.provider, "last_usage", None), response_model)
        return result

    def test_connection(self):
        result = self.generate("Verbindungstest: Gib ok als true zurück.", ConnectionResult)
        if not result.ok:
            raise LLMError("Das Modell hat den Verbindungstest nicht bestätigt.")
