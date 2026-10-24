from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

# Each test session gets an isolated database file.
_tmp = Path(tempfile.gettempdir()) / "stillhere_test.db"
os.environ["DB_PATH"] = str(_tmp)
os.environ.setdefault("WEBHOOK_SECRET", "test-secret")
os.environ.setdefault("DASHBOARD_TOKEN", "test-dash")
# Tests must never touch a real provider.
os.environ.pop("CARTESIA_API_KEY", None)
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("TWILIO_ACCOUNT_SID", None)


@pytest.fixture(autouse=True)
def fresh_db():
    from app.config import get_settings
    from app.db import init_db
    from app.services import demo_reset, events

    get_settings.cache_clear()
    if _tmp.exists():
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(_tmp) + suffix)
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass
    init_db()
    events.reset_sequence()
    demo_reset.reset()
    yield


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def dash_headers():
    return {"X-Dashboard-Token": "test-dash"}


@pytest.fixture
def tool_headers():
    return {"X-StillHere-Secret": "test-secret"}
