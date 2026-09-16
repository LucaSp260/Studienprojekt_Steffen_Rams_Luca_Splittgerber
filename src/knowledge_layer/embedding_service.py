"""Providerneutraler Zugang zu validierten Suchvektoren."""

import math

from src.llm.config import load_embedding_config
from src.llm.embedding_providers import EmbeddingError, openai_embeddings, gemini_embeddings

MAX_PART_BYTES = 6000
BATCH_SIZE = 32


def split_embedding_text(text):
    """UTF-8-Limit hält auch ungewöhnlichen Text unter dem Tokenlimit.

    Nichts abschneiden: Überlange Notes werden in Teilen eingebettet und zu
    einem längengewichteten Vektor zusammengeführt (eine ID je Knowledge Note).
    """
    parts, characters, size = [], [], 0
    for character in text:
        length = len(character.encode("utf-8"))
        if size + length > MAX_PART_BYTES:
            parts.append("".join(characters))
            characters, size = [], 0
        characters.append(character)
        size += length
    if characters:
        parts.append("".join(characters))
    return parts


def normalize(vector, dimensions):
    if not isinstance(vector, (list, tuple)) or len(vector) != dimensions:
        raise EmbeddingError("Inkompatible Embedding-Dimension. Bitte das konfigurierte Modell prüfen.")
    if any(isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value)
           for value in vector):
        raise EmbeddingError("Das Modell lieferte ungültige Embedding-Werte.")
    length = math.sqrt(sum(value * value for value in vector))
    if not math.isfinite(length) or length == 0:
        raise EmbeddingError("Das Modell lieferte ein leeres Embedding.")
    return [value / length for value in vector]


class EmbeddingService:
    def __init__(self, config=None):
        self.config = config or load_embedding_config()
        providers = {"openai": openai_embeddings, "gemini": gemini_embeddings}
        if self.config.provider not in providers or not self.config.model.strip():
            raise EmbeddingError("Bitte einen gültigen Anbieter und ein Embedding-Modell konfigurieren.")
        self._generate = providers[self.config.provider]

    def embed_documents(self, texts):
        return self._embed(texts, is_query=False)

    def embed_query(self, query):
        return self._embed([query], is_query=True)[0]

    def _embed(self, texts, is_query):
        if not texts:
            return []
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise EmbeddingError("Leerer Text kann nicht eingebettet werden.")
        if not self.config.api_key:
            raise EmbeddingError("Kein API-Key für Embeddings konfiguriert. Bitte Einstellungen prüfen.")
        grouped = [split_embedding_text(text) for text in texts]
        parts = [part for group in grouped for part in group]
        vectors = []
        for start in range(0, len(parts), BATCH_SIZE):
            batch = parts[start:start + BATCH_SIZE]
            result = self._generate(batch, self.config, is_query=is_query)
            if not isinstance(result, list) or len(result) != len(batch):
                raise EmbeddingError("Das Modell lieferte nicht für jeden Text ein Embedding.")
            vectors.extend(normalize(vector, self.config.dimensions) for vector in result)
        output, offset = [], 0
        for group in grouped:
            weights = [len(part.encode("utf-8")) for part in group]
            selected = vectors[offset:offset + len(group)]
            mean = [sum(vector[column] * weight for vector, weight in zip(selected, weights)) / sum(weights)
                    for column in range(self.config.dimensions)]
            output.append(normalize(mean, self.config.dimensions))
            offset += len(group)
        return output
