"""Offizielles Google-GenAI-SDK mit strukturierter Interactions-Antwort."""

import httpx
import base64
from google import genai
from google.genai import types
from pydantic import ValidationError

from src.llm.base_provider import BaseProvider, LLMError, SYSTEM_INSTRUCTION


class GeminiProvider(BaseProvider):
    def __init__(self, config):
        self.config = config

    def generate(self, prompt, response_model, images=None):
        self.last_usage = None
        try:
            with genai.Client(
                api_key=self.config.api_key,
                http_options=types.HttpOptions(
                    timeout=90000, retry_options=types.HttpRetryOptions(attempts=0)
                ),
            ) as client:
                request_input = prompt
                if images:
                    request_input = [{"type": "text", "text": prompt}]
                    request_input.extend({"type": "image", "data": base64.b64encode(image).decode("ascii"),
                                          "mime_type": "image/png"} for image in images)
                response = client.interactions.create(
                    model=self.config.model,
                    input=request_input,
                    system_instruction=SYSTEM_INSTRUCTION,
                    response_format={"type": "text", "mime_type": "application/json",
                                     "schema": response_model.model_json_schema()},
                    generation_config={"max_output_tokens": 12000},
                    store=False,
                    timeout=90,
                )
            if response.status != "completed" or not response.output_text:
                raise LLMError("Gemini lieferte keine vollständige strukturierte Antwort.")
            self.last_usage = getattr(response, "usage", None) or getattr(response, "usage_metadata", None)
            return response_model.model_validate_json(response.output_text)
        except LLMError:
            raise
        except (ValidationError, ValueError):
            raise LLMError("Gemini lieferte ungültige strukturierte Daten.") from None
        except Exception as error:
            # Die Interactions-API verwendet eine eigene SDK-Fehlerhierarchie.
            # Nur Statuscodes auswerten, niemals rohe Antworten/Schlüssel anzeigen.
            code = getattr(error, "code", None) or getattr(error, "status_code", None)
            if code in (401, 403):
                message = "Gemini: API-Key ungültig oder Zugriff verweigert. Bitte Einstellungen prüfen."
            elif code == 429:
                message = "Gemini: API-Limit oder Guthaben erschöpft. Bitte später erneut versuchen."
            elif isinstance(code, int) and code >= 500:
                message = "Gemini ist vorübergehend überlastet oder nicht verfügbar. Bitte später erneut versuchen."
            elif isinstance(error, (httpx.HTTPError, OSError)) or "Connection" in type(error).__name__ or "Timeout" in type(error).__name__:
                message = "Gemini ist nicht erreichbar oder die Anfrage dauerte zu lange. Netzwerk prüfen."
            else:
                message = "Gemini-Anfrage fehlgeschlagen. API-Key, Modell und Anbieterstatus prüfen."
            raise LLMError(message) from None
