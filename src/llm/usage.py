"""Kleine providerneutrale Usage-Erfassung ohne Tokenschätzung."""

import json
import logging
import sqlite3
from contextlib import contextmanager, closing
from contextvars import ContextVar

from src.persistence.database import get_connection

_context = ContextVar("llm_usage_context", default=None)


@contextmanager
def usage_operation(operation, document_id=None, note_ids=None):
    token = _context.set((operation, document_id, sorted(set(note_ids or []))))
    try:
        yield
    finally:
        _context.reset(token)


def generate_recorded(service, prompt, response_model, operation, document_id=None, note_ids=None):
    with usage_operation(operation, document_id, note_ids):
        return service.generate(prompt, response_model)


def record_service_usage(config, raw_usage, response_model):
    operation, document_id, note_ids = _context.get() or (response_model.__name__, None, [])
    record_usage(config, raw_usage, operation, document_id=document_id, note_ids=note_ids)


def record_usage(config, raw_usage, operation, document_id=None, note_ids=None):
    input_tokens = _field(raw_usage, "input_tokens", "prompt_tokens", "prompt_token_count")
    output_tokens = _field(raw_usage, "output_tokens", "candidates_token_count")
    total_tokens = _field(raw_usage, "total_tokens", "total_token_count")
    try:
        with closing(get_connection()) as connection, connection:
            connection.execute(
                """INSERT INTO llm_usage
                   (operation,provider,model,input_tokens,output_tokens,total_tokens,document_id,note_ids_json)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (operation, config.provider, config.model, input_tokens, output_tokens,
                 total_tokens, document_id, json.dumps(sorted(set(note_ids))) if note_ids else None),
            )
    except (OSError, sqlite3.Error):
        # Ein Telemetriefehler darf nach einem bezahlten KI-Aufruf keine Extraktion wiederholen.
        logging.getLogger(__name__).warning("LLM-Usage konnte nicht gespeichert werden.")


def _field(value, *names):
    for name in names:
        result = value.get(name) if isinstance(value, dict) else getattr(value, name, None)
        if type(result) is int and result >= 0:
            return result
    return None
