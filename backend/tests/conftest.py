from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

# Each test PROCESS gets its own database file.
#
# A fixed filename meant two concurrent `pytest` runs shared one SQLite file and
# the autouse `fresh_db` fixture wiped the other run's rows mid-test, surfacing
# as "no plan '<id>'" from `get_plan`. Scoping by pid makes a test run immune to
# anything else happening on the machine.
_tmp = Path(tempfile.gettempdir()) / f"stillhere_test_{os.getpid()}.db"
os.environ["DB_PATH"] = str(_tmp)
os.environ["WEBHOOK_SECRET"] = "test-secret"
os.environ["DASHBOARD_TOKEN"] = "test-dash"

# Tests get their OWN SpacetimeDB database, never the one the running app uses.
#
# This is not tidiness, it is correctness. `_reset_coordination()` below clears
# coordination tables before every test, and SpacetimeDB is a persistent
# external process shared by anything pointed at the same database name. With
# both on `stillhere`, a test run silently wiped a live demo's proposals
# mid-flight and the reducer then (correctly) refused with `no_proposal`.
# Publish it once with:
#   cd spacetime && spacetime publish --server local --yes stillhere-test
os.environ["SPACETIMEDB_DB"] = os.getenv("SPACETIMEDB_TEST_DB") or "stillhere-test"

# Known limitation, stated rather than hidden: SpacetimeDB databases must be
# published ahead of time, so unlike SQLite above this name cannot be made
# per-process automatically. Two concurrent `pytest` runs therefore still share
# `stillhere-test` and will clear each other's coordination rows. To run them in
# parallel, give each its own published database:
#   cd spacetime && spacetime publish --server local --yes stillhere-test-2
#   SPACETIMEDB_TEST_DB=stillhere-test-2 python -m pytest backend/tests -q

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


@pytest.fixture(scope="session", autouse=True)
def _cleanup_db_file():
    """Remove this process's SQLite file when the session ends."""
    yield
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(_tmp) + suffix)
        if candidate.exists():
            try:
                candidate.unlink()
            except OSError:
                pass


@pytest.fixture(scope="session", autouse=True)
def _reset_coordination_once():
    """Clear SpacetimeDB coordination state ONCE per test session.

    Session-scoped clear, kept as a cheap safety net for whatever the previous
    run left behind. Per-test isolation is `fresh_db` below.
    """
    _reset_coordination()
    yield


@pytest.fixture(autouse=True)
def fresh_db():
    from app.config import get_settings
    from app.db import init_db
    from app.services import demo_reset, events

    get_settings.cache_clear()
    # Per-test coordination reset is REQUIRED, not defensive. Several tests use
    # fixed idempotency keys ("grp-2", "idem-13"), and
    # `plan_confirmations.idempotencyKey` is UNIQUE in the module schema - so a
    # second test reusing a key hits the constraint and the reducer refuses a
    # confirmation that should have succeeded. Those tests pass alone and fail
    # in a full run without this.
    #
    # Cost is one reducer round-trip (~0.5s), which the `reset_coordination`
    # reducer made affordable; the earlier 8-statement SQL fallback cost 2.4s
    # and was what made the suite unbearably slow.
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
