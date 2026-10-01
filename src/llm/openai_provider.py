"""Offizielles OpenAI-SDK mit Structured Outputs über die Responses API."""

import openai
from pydantic import ValidationError

from src.llm.base_provider import BaseProvider, LLMError, SYSTEM_INSTRUCTION


class OpenAIProvider(BaseProvider):
    def __init__(self, config):
        self.config = config

    def generate(self, prompt, response_model):
        self.last_usage = None
        try:
            with openai.OpenAI(api_key=self.config.api_key, timeout=90, max_retries=0) as client:
                response = client.responses.parse(
                    model=self.config.model,
                    instructions=SYSTEM_INSTRUCTION,
                    input=prompt,
                    text_format=response_model,
                    max_output_tokens=12000,
                    store=False,
                )
            if response.status != "completed" or response.output_parsed is None:
                raise LLMError("OpenAI lieferte keine vollständige strukturierte Antwort.")
            self.last_usage = getattr(response, "usage", None)
            return response.output_parsed
        except openai.AuthenticationError:
            raise LLMError("Der OpenAI-API-Key ist ungültig. Bitte Einstellungen prüfen.") from None
        except openai.RateLimitError:
            raise LLMError("OpenAI: API-Limit oder Guthaben erschöpft. Bitte später erneut versuchen.") from None
        except openai.APIConnectionError:
            raise LLMError("OpenAI ist nicht erreichbar oder die Anfrage dauerte zu lange. Netzwerk prüfen.") from None
        except openai.APIError:
            raise LLMError("OpenAI-Anfrage fehlgeschlagen. Modell, Zugriffsrechte und Anbieterstatus prüfen.") from None
        except (ValidationError, ValueError):
            raise LLMError("OpenAI lieferte ungültige strukturierte Daten.") from None
        except openai.OpenAIError:
            raise LLMError("OpenAI lieferte keine verwertbare Antwort. Bitte Modell und Ausgabelimit prüfen.") from None
