"""SpacetimeDB coordination tests — exercised against a LIVE local module.

Skips cleanly when the module is unreachable, so the suite still passes on a
machine without SpacetimeDB. When it IS reachable these are real reducer
transactions, not mocks: each test writes through `POST /v1/database/{db}/call/
{reducer}` and reads back with SQL.

The reducers under test are the authoritative coordination layer per
specs/CONTRACTS_V2.md §1-§3. The invariant that matters most is in
`TestConfirmationInvariant`: a plan can confirm ONLY when both parties accepted
the identical activity AND time on the SAME, still-active proposal version.
"""
from __future__ import annotations

import uuid

import pytest

from app.adapters import spacetimedb_adapter as stdb

pytestmark = pytest.mark.stdb  # registered in pytest.ini


def _reachable() -> bool:
    try:
        return bool(stdb.health().get("reachable"))
    except Exception:
        return False


pytest.importorskip("httpx")

if not _reachable():  # pragma: no cover - environment dependent
    pytest.skip(
        "SpacetimeDB module 'stillhere' is not reachable; run `make stdb`",
        allow_module_level=True,
    )


ACTIVITY = "a Marvel movie"
WHEN = "2026-10-31T19:10:00+00:00"
OTHER_WHEN = "2026-11-01T11:00:00+00:00"


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _reason(exc: Exception) -> str:
    """Machine-readable rejection reason out of a reducer error."""
    return str(exc)


@pytest.fixture
def session():
    """A fresh coordination session with one active proposal at v1."""
    sid = _uid("sess")
    pid = _uid("prop")
    stdb.call_reducer("start_session", [sid, "user_sam", "user_josh", "simulated"])
    stdb.call_reducer("set_consent_state", [
        sid, True, True, True, ["voice_call"], "seeded by test",
    ])
    stdb.call_reducer("propose_or_revise_plan", [
        pid, sid, 1, ACTIVITY, "AMC Metreon 16", WHEN,
        "best friend since high school", "they watch Marvel together",
        ["mem_a", "mem_b"], 0,
    ])
    return {"session_id": sid, "proposal_id": pid}


def _accept(proposal_id, version, role, activity=ACTIVITY, when=WHEN, simulated=False):
    reducer = "respond_as_friend" if role == "contact" else "record_patient_approval"
    stdb.call_reducer(reducer, [
        _uid("resp"), proposal_id, version, "accepted", activity, when, simulated,
    ])


class TestAdapter:
    def test_configured_and_reachable(self):
        assert stdb.configured() is True
        h = stdb.health()
        assert h["reachable"] is True
        assert h["db"]

    def test_sql_round_trip_returns_dicts(self, session):
        rows = stdb.query_sql(
            f"SELECT * FROM plan_proposals WHERE proposal_id = '{session['proposal_id']}'"
        )
        assert len(rows) == 1
        assert rows[0]["activity"] == ACTIVITY

    def test_adapter_never_echoes_token(self):
        blob = repr(stdb.health())
        assert "eyJ" not in blob  # no JWT fragment
        assert "Bearer" not in blob


class TestSessionAndProposals:
    def test_session_row_written(self, session):
        rows = stdb.query_sql(
            f"SELECT * FROM connection_sessions WHERE session_id = '{session['session_id']}'"
        )
        assert len(rows) == 1

    def test_revision_creates_new_version_and_deactivates_old(self, session):
        pid2 = _uid("prop")
        stdb.call_reducer("propose_or_revise_plan", [
            pid2, session["session_id"], 2, "Coffee", "Four Barrel", OTHER_WHEN,
            "best friend", "they used to get coffee", ["mem_c"], 1,
        ])
        old = stdb.query_sql(
            f"SELECT * FROM plan_proposals WHERE proposal_id = '{session['proposal_id']}'"
        )[0]
        new = stdb.query_sql(f"SELECT * FROM plan_proposals WHERE proposal_id = '{pid2}'")[0]
        # Immutable history: the old row still exists, but is no longer active.
        assert old["active"] is False
        assert new["active"] is True
        assert new["version"] == 2

    def test_revision_invalidates_prior_approvals(self, session):
        """A stale approval must not survive a change of terms."""
        _accept(session["proposal_id"], 1, "contact")
        _accept(session["proposal_id"], 1, "patient")

        pid2 = _uid("prop")
        stdb.call_reducer("propose_or_revise_plan", [
            pid2, session["session_id"], 2, "Coffee", "", OTHER_WHEN,
            "best friend", "coffee is grounded in memory", ["mem_c"], 1,
        ])

        with pytest.raises(stdb.SpacetimeError) as e:
            stdb.call_reducer("confirm_if_matching", [session["proposal_id"], 1, _uid("idem")])
        assert any(
            k in _reason(e.value)
            for k in ("superseded", "stale_version", "missing_contact_approval",
                      "missing_patient_approval")
        ), _reason(e.value)


class TestConfirmationInvariant:
    """The hard security invariant from specs/CONTRACTS_V2.md §3."""

    def test_confirms_when_both_accept_identical_terms(self, session):
        _accept(session["proposal_id"], 1, "contact")
        _accept(session["proposal_id"], 1, "patient", simulated=True)
        stdb.call_reducer("confirm_if_matching", [session["proposal_id"], 1, _uid("idem")])

        rows = stdb.query_sql(
            f"SELECT * FROM plan_confirmations WHERE proposal_id = '{session['proposal_id']}'"
        )
        assert len(rows) == 1
        assert rows[0]["activity"] == ACTIVITY
        assert rows[0]["starts_at"] == WHEN

    def test_refuses_without_any_approval(self, session):
        with pytest.raises(stdb.SpacetimeError) as e:
            stdb.call_reducer("confirm_if_matching", [session["proposal_id"], 1, _uid("idem")])
        assert "missing" in _reason(e.value)

    def test_refuses_with_only_contact_approval(self, session):
        _accept(session["proposal_id"], 1, "contact")
        with pytest.raises(stdb.SpacetimeError) as e:
            stdb.call_reducer("confirm_if_matching", [session["proposal_id"], 1, _uid("idem")])
        assert "missing_patient_approval" in _reason(e.value)

    def test_refuses_with_only_patient_approval(self, session):
        _accept(session["proposal_id"], 1, "patient", simulated=True)
        with pytest.raises(stdb.SpacetimeError) as e:
            stdb.call_reducer("confirm_if_matching", [session["proposal_id"], 1, _uid("idem")])
        assert "missing_contact_approval" in _reason(e.value)

    def test_refuses_on_time_mismatch(self, session):
        """Josh says Sunday, Sam approves Saturday -> must NOT confirm."""
        _accept(session["proposal_id"], 1, "contact", when=OTHER_WHEN)
        _accept(session["proposal_id"], 1, "patient", when=WHEN, simulated=True)
        with pytest.raises(stdb.SpacetimeError) as e:
            stdb.call_reducer("confirm_if_matching", [session["proposal_id"], 1, _uid("idem")])
        assert "time_mismatch" in _reason(e.value)

    def test_refuses_on_activity_mismatch(self, session):
        _accept(session["proposal_id"], 1, "contact", activity="Coffee")
        _accept(session["proposal_id"], 1, "patient", activity=ACTIVITY, simulated=True)
        with pytest.raises(stdb.SpacetimeError) as e:
            stdb.call_reducer("confirm_if_matching", [session["proposal_id"], 1, _uid("idem")])
        assert "activity_mismatch" in _reason(e.value)

    def test_refuses_stale_version(self, session):
        _accept(session["proposal_id"], 1, "contact")
        _accept(session["proposal_id"], 1, "patient", simulated=True)
        with pytest.raises(stdb.SpacetimeError) as e:
            stdb.call_reducer("confirm_if_matching", [session["proposal_id"], 99, _uid("idem")])
        assert "stale_version" in _reason(e.value)

    def test_refuses_unknown_proposal(self):
        with pytest.raises(stdb.SpacetimeError) as e:
            stdb.call_reducer("confirm_if_matching", [_uid("nope"), 1, _uid("idem")])
        assert "no_proposal" in _reason(e.value)

    def test_idempotent_replay_of_same_key(self, session):
        _accept(session["proposal_id"], 1, "contact")
        _accept(session["proposal_id"], 1, "patient", simulated=True)
        key = _uid("idem")
        stdb.call_reducer("confirm_if_matching", [session["proposal_id"], 1, key])
        # Exact replay must succeed without writing a second confirmation.
        stdb.call_reducer("confirm_if_matching", [session["proposal_id"], 1, key])
        rows = stdb.query_sql(
            f"SELECT * FROM plan_confirmations WHERE proposal_id = '{session['proposal_id']}'"
        )
        assert len(rows) == 1

    def test_second_different_key_is_rejected(self, session):
        _accept(session["proposal_id"], 1, "contact")
        _accept(session["proposal_id"], 1, "patient", simulated=True)
        stdb.call_reducer("confirm_if_matching", [session["proposal_id"], 1, _uid("idem")])
        with pytest.raises(stdb.SpacetimeError) as e:
            stdb.call_reducer("confirm_if_matching", [session["proposal_id"], 1, _uid("idem")])
        assert "already_confirmed" in _reason(e.value)

    def test_revoked_consent_blocks_confirmation(self, session):
        _accept(session["proposal_id"], 1, "contact")
        _accept(session["proposal_id"], 1, "patient", simulated=True)
        stdb.call_reducer("revoke_or_cancel", [session["session_id"], "user revoked in test"])
        with pytest.raises(stdb.SpacetimeError) as e:
            stdb.call_reducer("confirm_if_matching", [session["proposal_id"], 1, _uid("idem")])
        assert any(
            k in _reason(e.value) for k in ("consent_revoked", "superseded", "stale_version")
        ), _reason(e.value)


class TestPrivacyBoundary:
    """specs/CONTRACTS_V2.md §1: no private data in any public table."""

    PUBLIC_TABLES = [
        "connection_sessions", "call_sessions", "plan_proposals",
        "participant_responses", "consent_state", "agent_events",
        "plan_confirmations", "demo_presence",
    ]

    CLINICAL = (
        "hospital", "psychiatric", "depress", "diagnos", "discharge",
        "medication", "therapist", "inpatient",
    )

    def test_no_clinical_language_in_public_tables(self, session):
        for table in self.PUBLIC_TABLES:
            rows = stdb.query_sql(f"SELECT * FROM {table}")
            blob = repr(rows).lower()
            for term in self.CLINICAL:
                assert term not in blob, f"{table} leaked {term!r}"

    def test_no_full_phone_numbers_in_public_tables(self, session):
        import re

        # An E.164 number: optional +, then 10-15 digits, as a standalone token.
        # Anchored on word boundaries so a random id suffix (resp_9523849536)
        # does not trip it, and a real number (+14155551234) does.
        phone = re.compile(r"(?<![\w])\+?\d{10,15}(?![\w])")
        # Random ids legitimately contain long hex/digit runs.
        id_fields = {"id", "key", "ref", "seq"}

        for table in self.PUBLIC_TABLES:
            rows = stdb.query_sql(f"SELECT * FROM {table}")
            for row in rows:
                for k, v in row.items():
                    if not isinstance(v, str):
                        continue
                    lk = k.lower()
                    # Timestamps and opaque identifiers are not phone numbers.
                    if "at" in lk or "time" in lk:
                        continue
                    if any(lk.endswith(suffix) or suffix in lk for suffix in id_fields):
                        continue
                    assert not phone.search(v), (
                        f"{table}.{k} looks like a phone number: {v!r}"
                    )

    def test_phone_regex_itself_is_sound(self):
        """Guard the guard: the detector must catch a real number and ignore an id."""
        import re

        phone = re.compile(r"(?<![\w])\+?\d{10,15}(?![\w])")
        assert phone.search("+14155551234")
        assert phone.search("call 4155551234 now")
        assert not phone.search("resp_9523849536")
        assert not phone.search("prop_ab12cd34ef")

    def test_call_sessions_store_only_redacted_numbers(self, session):
        cid = _uid("call")
        stdb.call_reducer("set_call_state", [
            cid, session["session_id"], "", "***4821", "ringing", "simulated",
            True, "",
        ])
        row = stdb.query_sql(f"SELECT * FROM call_sessions WHERE call_id = '{cid}'")[0]
        assert row["to_number_redacted"].startswith("***")
        assert len(row["to_number_redacted"]) <= 8
