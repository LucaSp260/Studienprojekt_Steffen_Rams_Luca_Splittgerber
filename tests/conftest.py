"""Development tests are offline, even when the host has configured API keys."""
import pytest
import httpx
import httpx2

@pytest.fixture(autouse=True)
def no_external_provider_requests(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Real provider HTTP requests are forbidden in this testsuite.")
    monkeypatch.setattr(httpx.Client, "send", blocked)
    monkeypatch.setattr(httpx2.Client, "send", blocked)

@pytest.fixture(autouse=True)
def isolated_default_database(tmp_path, monkeypatch):
    from src.persistence import database
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path/"application.db")
    database.initialize_database()
