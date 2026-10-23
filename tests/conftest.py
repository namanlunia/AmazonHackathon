import os
import sys
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Enforce offline mode for all automated testing
os.environ["USE_AWS"] = "false"
os.environ["HUB_API_KEY"] = ""
os.environ["DEFAULT_USER_ID"] = "test-user"

from app.config import get_settings
from app.main import app
from app.state_store import InMemoryStateStore, get_store


@pytest.fixture(autouse=True)
def reset_in_memory_state() -> Generator[None, None, None]:
    get_settings.cache_clear()
    store = get_store()
    if isinstance(store, InMemoryStateStore):
        store._states.clear()
        store._locks.clear()
    yield
    if isinstance(store, InMemoryStateStore):
        store._states.clear()
        store._locks.clear()


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client
