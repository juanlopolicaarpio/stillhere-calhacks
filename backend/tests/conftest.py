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
os.environ["WEBHOOK_SECRET"] = "test-secret"
os.environ["DASHBOARD_TOKEN"] = "test-dash"

# Tests must NEVER touch a real provider, place a real call, or spend credits.
#
# Setting these to "" rather than popping them is deliberate and load-bearing:
# `app.config` calls `load_dotenv()` at import time, and python-dotenv defaults
# to override=False — it will NOT overwrite a key that already exists in the
# environment, but it WILL repopulate one that was deleted. Popping therefore
# let the real .env credentials leak into the test run (observed 2026-10-24:
# six "not_configured" tests failed because a live CARTESIA_API_KEY was
# visible to them). An explicit empty string is sticky and keeps the suite
# hermetic.
for _secret in (
    "CARTESIA_API_KEY",
    "CARTESIA_AGENT_ID",
    "CARTESIA_FROM_NUMBER_ID",
    "ANTHROPIC_API_KEY",
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "MY_PHONE_E164",
):
    os.environ[_secret] = ""


#: Coordination tables in SpacetimeDB. Order matters only for readability -
#: there are no FK constraints to respect.
_COORD_TABLES = (
    "plan_confirmations",
    "participant_responses",
    "plan_proposals",
    "consent_state",
    "call_sessions",
    "connection_sessions",
    "agent_events",
    "demo_presence",
)


def _reset_coordination() -> None:
    """Clear SpacetimeDB coordination state between tests.

    `wipe_all()` resets SQLite, but the SpacetimeDB module is a PERSISTENT
    EXTERNAL process that outlives both the test and the session. Leaving its
    rows in place made the suite non-hermetic: tests passed individually and in
    their own file, then failed when run together, because a previous test's
    coordination rows were still visible to the reducers. Clearing both stores
    is what actually makes a test independent.

    Silent no-op when SpacetimeDB is unreachable, so the suite still runs on a
    machine without it.
    """
    try:
        from app.adapters import spacetimedb_adapter as stdb
    except Exception:
        return
    try:
        if not stdb.configured():
            return
    except Exception:
        return
    # One reducer call, one transaction, one round-trip. The SQL fallback below
    # costs eight round-trips (~2.4s per test, ~10 min across the suite), so it
    # runs only against a module that predates the reducer.
    try:
        stdb.call_reducer("reset_coordination", [])
        return
    except Exception:
        pass
    for table in _COORD_TABLES:
        try:
            stdb.query_sql(f"DELETE FROM {table}")
        except Exception:
            # Unreachable, or the module predates this table. Either way the
            # test should run rather than error in setup.
            return


@pytest.fixture(autouse=True)
def fresh_db():
    from app.config import get_settings
    from app.db import init_db
    from app.services import demo_reset, events

    get_settings.cache_clear()
    _reset_coordination()
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
