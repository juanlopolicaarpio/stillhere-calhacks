"""SQLite persistence. Durable across restarts (a hard requirement of the memory tests)."""
from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import get_settings

_local = threading.local()

# --- additive table migrations -------------------------------------------
# Whole tables added after the original schema. Declared as their own DDL
# constants (and appended to SCHEMA below) so `init_db()` can create them on
# databases that predate them - the column-level migration loop in `init_db()`
# can add a column but not a table.
#
# contact_events is the longitudinal contact history behind
# `services/baseline.py` / `services/opportunity.py`. Deliberately no FK
# constraints, matching specs/CONTRACTS_V3.md §2.
CONTACT_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS contact_events (
  id TEXT PRIMARY KEY,
  patient_id TEXT NOT NULL,
  contact_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'observed',
  note TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_contact_events_pair
  ON contact_events(patient_id, contact_id, occurred_at);
"""

# calendar_tokens holds the OAuth grants behind `adapters/google_calendar.py`
# and `adapters/microsoft_graph.py`, per specs/CONTRACTS_V3.md §3. The token
# columns hold SECRETS: they are never logged, never put in an event payload
# and never returned from an API route. They are sealed at rest when
# CALENDAR_TOKEN_KEY is set (see the adapter's `seal`/`unseal`), and stored in
# plaintext otherwise - a documented known gap in
# docs/CALENDAR_INTEGRATION.md. One grant per (user_id, provider).
CALENDAR_TOKENS_DDL = """
CREATE TABLE IF NOT EXISTS calendar_tokens (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  provider TEXT NOT NULL,
  access_token TEXT NOT NULL,
  refresh_token TEXT NOT NULL DEFAULT '',
  expires_at TEXT,
  scopes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_calendar_tokens_user_provider
  ON calendar_tokens(user_id, provider);
"""

ADDITIVE_TABLES: tuple[tuple[str, str], ...] = (
    ("contact_events", CONTACT_EVENTS_DDL),
    ("calendar_tokens", CALENDAR_TOKENS_DDL),
)

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  role TEXT NOT NULL,
  phone_e164 TEXT,
  phone_verified INTEGER NOT NULL DEFAULT 0,
  timezone TEXT NOT NULL DEFAULT 'America/Los_Angeles',
  preferred_channel TEXT DEFAULT 'call',
  is_adult_attested INTEGER NOT NULL DEFAULT 0,
  outreach_active INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS verification_codes (
  id TEXT PRIMARY KEY,
  phone_e164 TEXT NOT NULL,
  code TEXT NOT NULL,
  purpose TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  consumed_at TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS relationships (
  id TEXT PRIMARY KEY,
  patient_id TEXT NOT NULL REFERENCES users(id),
  contact_id TEXT NOT NULL REFERENCES users(id),
  relationship_label TEXT NOT NULL,
  status TEXT NOT NULL,
  last_contact_at TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS invitations (
  token TEXT PRIMARY KEY,
  patient_id TEXT NOT NULL REFERENCES users(id),
  contact_name TEXT NOT NULL,
  contact_phone_e164 TEXT NOT NULL,
  relationship_label TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  accepted_at TEXT,
  declined_at TEXT,
  contact_id TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL REFERENCES users(id),
  session_id TEXT NOT NULL,
  message_role TEXT NOT NULL,
  text TEXT NOT NULL,
  sensitivity TEXT NOT NULL DEFAULT 'normal',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memories (
  id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL REFERENCES users(id),
  subject_id TEXT,
  kind TEXT NOT NULL,
  value TEXT NOT NULL,
  source_conversation_id TEXT,
  source_quote TEXT,
  confidence REAL NOT NULL DEFAULT 0.6,
  confirmed INTEGER NOT NULL DEFAULT 0,
  visibility TEXT NOT NULL,
  superseded_by TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS consents (
  id TEXT PRIMARY KEY,
  patient_id TEXT NOT NULL REFERENCES users(id),
  contact_id TEXT NOT NULL REFERENCES users(id),
  purpose TEXT NOT NULL,
  allowed_actions TEXT NOT NULL,
  allowed_fields TEXT NOT NULL,
  quiet_hours_start INTEGER DEFAULT 21,
  quiet_hours_end INTEGER DEFAULT 9,
  max_per_day INTEGER DEFAULT 2,
  active INTEGER NOT NULL DEFAULT 1,
  revoked_at TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS availability (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  start_at TEXT NOT NULL,
  end_at TEXT NOT NULL,
  source TEXT NOT NULL,
  visibility TEXT NOT NULL DEFAULT 'agent_internal'
);

CREATE TABLE IF NOT EXISTS plans (
  id TEXT PRIMARY KEY,
  patient_id TEXT NOT NULL REFERENCES users(id),
  contact_id TEXT NOT NULL REFERENCES users(id),
  activity TEXT NOT NULL,
  location TEXT,
  starts_at TEXT NOT NULL,
  status TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  rationale TEXT,
  why_contact TEXT,
  why_activity TEXT,
  memory_citations TEXT,
  confirmed_at TEXT,
  idempotency_key TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plan_participants (
  plan_id TEXT NOT NULL REFERENCES plans(id),
  user_id TEXT NOT NULL REFERENCES users(id),
  role TEXT NOT NULL,
  response TEXT,
  responded_activity TEXT,
  responded_starts_at TEXT,
  response_at TEXT,
  simulated INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (plan_id, user_id)
);

CREATE TABLE IF NOT EXISTS calls (
  id TEXT PRIMARY KEY,
  plan_id TEXT,
  patient_id TEXT,
  contact_id TEXT,
  session_id TEXT,
  provider TEXT NOT NULL,
  provider_call_id TEXT,
  to_number_redacted TEXT,
  status TEXT NOT NULL,
  mode TEXT NOT NULL,
  idempotency_key TEXT UNIQUE,
  requested_at TEXT,
  answered_at TEXT,
  ended_at TEXT,
  error TEXT,
  raw_provider_response TEXT,
  purpose TEXT NOT NULL DEFAULT 'friend_reunion_outreach',
  authorized_context TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS agent_events (
  id TEXT PRIMARY KEY,
  session_id TEXT,
  plan_id TEXT,
  call_id TEXT,
  event_type TEXT NOT NULL,
  safe_summary TEXT NOT NULL,
  payload TEXT,
  simulated INTEGER NOT NULL DEFAULT 0,
  seq INTEGER,
  timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outreach_log (
  id TEXT PRIMARY KEY,
  patient_id TEXT NOT NULL,
  contact_id TEXT NOT NULL,
  channel TEXT NOT NULL,
  occurred_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS idempotency (
  key TEXT PRIMARY KEY,
  scope TEXT NOT NULL,
  result TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS demo_sessions (
  id TEXT PRIMARY KEY,
  state TEXT NOT NULL,
  reset_version INTEGER NOT NULL,
  started_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mem_owner ON memories(owner_id, deleted_at);
CREATE INDEX IF NOT EXISTS idx_conv_owner ON conversations(owner_id, created_at);
CREATE INDEX IF NOT EXISTS idx_events_seq ON agent_events(seq);
CREATE INDEX IF NOT EXISTS idx_calls_idem ON calls(idempotency_key);
"""

# Additive tables are part of the canonical schema for a fresh database too.
SCHEMA += "".join(ddl for _name, ddl in ADDITIVE_TABLES)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str = "") -> str:
    raw = uuid.uuid4().hex[:12]
    return f"{prefix}_{raw}" if prefix else raw


def get_conn() -> sqlite3.Connection:
    """One connection per thread; SQLite connections are not thread-safe."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        path = get_settings().db_path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


def init_db() -> None:
    conn = get_conn()
    conn.executescript(SCHEMA)
    # Additive migrations for databases created by an earlier schema.
    for table, column, decl in (
        ("relationships", "last_contact_at", "TEXT"),
        # Without this, unanswered-call suppression in services/opportunity.py
        # could only match on contact_id and over-suppressed across patients.
        ("calls", "patient_id", "TEXT"),
        # The provider receives only backend-verified intent and context.  These
        # fields make the purpose auditable and stop a general-purpose voice
        # agent from inventing why it is calling.
        ("calls", "purpose", "TEXT NOT NULL DEFAULT 'friend_reunion_outreach'"),
        ("calls", "authorized_context", "TEXT NOT NULL DEFAULT '{}'"),
    ):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    # Additive TABLE migrations, for databases created before these tables
    # existed. Idempotent: the DDL is only run when the table is absent.
    for table, ddl in ADDITIVE_TABLES:
        present = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if present is None:
            conn.executescript(ddl)
    conn.commit()


def q(sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    return list(get_conn().execute(sql, tuple(params)).fetchall())


def q1(sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
    return get_conn().execute(sql, tuple(params)).fetchone()


def ex(sql: str, params: Iterable[Any] = ()) -> None:
    conn = get_conn()
    conn.execute(sql, tuple(params))
    conn.commit()


def wipe_all() -> None:
    """Demo reset. Clears application rows but keeps the schema."""
    conn = get_conn()
    tables = [
        "plan_participants", "plans", "calls", "agent_events", "memories",
        "conversations", "consents", "availability", "relationships",
        "invitations", "verification_codes", "outreach_log", "idempotency",
        "demo_sessions", "users",
    ]
    conn.execute("PRAGMA foreign_keys=OFF")
    for t in tables:
        conn.execute(f"DELETE FROM {t}")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()
