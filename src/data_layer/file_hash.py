"""Inhaltsbasierte Erkennung identischer Dateien."""

import hashlib
from pathlib import Path


def calculate_file_hash(source):
    """SHA-256 für Dateiinhalt (bytes) oder einen Dateipfad berechnen."""
    if isinstance(source, bytes):
        return hashlib.sha256(source).hexdigest()
    digest = hashlib.sha256()
    with Path(source).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
