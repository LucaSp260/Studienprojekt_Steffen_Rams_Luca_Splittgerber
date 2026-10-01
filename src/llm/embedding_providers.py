"""Anbieterabhängige SDK-Aufrufe ausschließlich für Embeddings."""

import openai
from google import genai
from google.genai import types

from src.llm.usage import record_usage


class EmbeddingError(Exception):
    """Sichere Fehlermeldung ohne Schlüssel oder rohe API-Antwort."""


def openai_embeddings(texts, config, is_query=False):
    try:
        with openai.OpenAI(api_key=config.api_key, timeout=60, max_retries=0) as client:
            response = client.embeddings.create(
                model=config.model, input=texts, dimensions=config.dimensions, encoding_format="float"
            )
        items = sorted(response.data, key=lambda item: item.index)
        if [item.index for item in items] != list(range(len(texts))):
            raise EmbeddingError("OpenAI lieferte unvollständige Embeddings.")
        record_usage(config, getattr(response, "usage", None),
                     "embedding_query" if is_query else "embedding_documents")
        return [item.embedding for item in items]
    except openai.AuthenticationError:
        raise EmbeddingError("OpenAI-API-Key ungültig. Bitte Einstellungen prüfen.") from None
    except openai.RateLimitError:
        raise EmbeddingError("OpenAI-API-Limit oder Guthaben erschöpft. Bitte später erneut versuchen.") from None
    except openai.APIConnectionError:
        raise EmbeddingError("OpenAI-Embedding-Anfrage: Netzwerkfehler oder Timeout.") from None
    except openai.OpenAIError:
        raise EmbeddingError("OpenAI-Embedding fehlgeschlagen. Embedding-Modell, Dimension und Zugriffsrechte prüfen.") from None


def gemini_embeddings(texts, config, is_query=False):
    # Embeddings 2 verwendet Textpräfixe. Content-Objekte verhindern, dass
    # mehrere Notes versehentlich zu einem einzigen Embedding aggregiert werden.
    prefix = "task: search result | query: " if is_query else "title: none | text: "
    options = {"output_dimensionality": config.dimensions}
    if config.model.removeprefix("models/") == "gemini-embedding-001":
        prefix = ""
        options["task_type"] = "RETRIEVAL_QUERY" if is_query else "RETRIEVAL_DOCUMENT"
    contents = [types.Content(parts=[types.Part.from_text(text=prefix + text)]) for text in texts]
    try:
        with genai.Client(api_key=config.api_key, http_options=types.HttpOptions(
            timeout=60000, retry_options=types.HttpRetryOptions(attempts=1)
        )) as client:
            response = client.models.embed_content(
                model=config.model, contents=contents, config=types.EmbedContentConfig(**options)
            )
        if not response.embeddings:
            raise EmbeddingError("Gemini lieferte keine Embeddings.")
        record_usage(config, getattr(response, "usage", None) or
                     getattr(response, "usage_metadata", None),
                     "embedding_query" if is_query else "embedding_documents")
        return [item.values for item in response.embeddings]
    except EmbeddingError:
        raise
    except Exception as error:
        code = getattr(error, "code", None) or getattr(error, "status_code", None)
        if code in (401, 403):
            message = "Gemini-API-Key ungültig oder Zugriff verweigert. Einstellungen prüfen."
        elif code == 429:
            message = "Gemini-API-Limit erschöpft. Bitte später erneut versuchen."
        else:
            message = "Gemini-Embedding fehlgeschlagen. Netzwerk/Timeout, Embedding-Modell und API-Key prüfen."
        raise EmbeddingError(message) from None
