"""Memory: persistence across restart, provenance, corrections, privacy filtering."""
from __future__ import annotations

import pytest

from app.services import memory
from app.services.demo_reset import JOSH, SAM


class TestPersistence:
    def test_survives_a_simulated_restart(self):
        """The headline requirement: a learned fact outlives the process.

        Closing the thread-local connection and reopening it is the in-process
        equivalent of restarting the backend against the same database file.
        """
        m = memory.store_memory(
            SAM, "fact", "Sam's dog is called Biscuit",
            source_quote="my dog Biscuit", confidence=0.9,
            visibility="shareable_with_contact",
        )

        import app.db as db

        db._local.conn.close()
        db._local.conn = None          # force a brand-new connection
        db.init_db()

        again = memory.get_memory(m.id)
        assert again is not None
        assert again.value == "Sam's dog is called Biscuit"
        assert again.source_quote == "my dog Biscuit"

    def test_retrieval_finds_it_by_query(self):
        memory.store_memory(SAM, "fact", "Sam's dog is called Biscuit",
                            visibility="agent_internal")
        hits = memory.retrieve(SAM, "biscuit dog", audience="planning")
        assert any("Biscuit" in h.value for h in hits)


class TestProvenance:
    def test_seeded_memories_all_cite_a_source(self):
        for m in memory.list_memories(SAM):
            assert m.source_quote, f"{m.id} has no source quote"

    def test_source_conversation_is_linked(self):
        linked = [m for m in memory.list_memories(SAM) if m.source_conversation_id]
        assert len(linked) >= 5


class TestSensitivityClassification:
    @pytest.mark.parametrize("text", [
        "Sam was discharged from the hospital",
        "Sam has a depression diagnosis",
        "Sam takes medication daily",
        "Sam saw his therapist",
        "Sam was an inpatient",
    ])
    def test_clinical_text_is_private_by_default(self, text):
        assert memory.classify_sensitivity(text) == "private_to_sam"

    @pytest.mark.parametrize("text", [
        "Sam likes Marvel movies",
        "Josh is Sam's best friend",
        "Sam prefers plans made in advance",
    ])
    def test_ordinary_text_is_internal_by_default(self, text):
        assert memory.classify_sensitivity(text) == "agent_internal"


class TestPrivacyFiltering:
    def test_shareable_context_excludes_private_clinical_memory(self):
        ctx = memory.shareable_context(SAM, JOSH, ["first_name"])
        blob = " ".join(
            ctx["shared_interests"]
            + [ctx["relationship"] or ""]
            + ctx["boundaries"]
        ).lower()
        for term in ("hospital", "psychiatric", "depress", "discharge", "diagnos"):
            assert term not in blob, f"leaked {term!r}: {blob}"

    def test_shareable_context_still_has_useful_content(self):
        ctx = memory.shareable_context(SAM, JOSH, ["first_name"])
        assert ctx["shared_interests"], "nothing shareable survived filtering"
        assert ctx["citations"], "no provenance returned"

    def test_mislabelled_private_row_is_still_blocked(self):
        """Defence in depth: a clinical fact wrongly marked shareable is
        caught by the content guard, not just the visibility label.
        """
        memory.store_memory(
            SAM, "fact", "Sam was hospitalized for depression in October",
            visibility="shareable_with_contact",   # deliberately wrong
        )
        ctx = memory.shareable_context(SAM, JOSH, [])
        blob = " ".join(ctx["shared_interests"]).lower()
        assert "hospitalized" not in blob
        assert ctx["blocked_count"] >= 1

    def test_audience_allow_lists_are_disjoint_where_it_matters(self):
        assert "private_to_sam" in memory.AUDIENCE_VISIBILITY["owner"]
        assert "private_to_sam" not in memory.AUDIENCE_VISIBILITY["planning"]
        assert "private_to_sam" not in memory.AUDIENCE_VISIBILITY["contact"]
        assert "agent_internal" not in memory.AUDIENCE_VISIBILITY["contact"]

    def test_unknown_audience_fails_closed(self):
        got = memory.retrieve(SAM, audience="totally-made-up")
        assert all(m.visibility == "public_demo_safe" for m in got)

    def test_outbound_guard_raises_on_sensitive_text(self):
        with pytest.raises(memory.MemoryError_):
            memory.assert_no_sensitive_leak("Sam just got out of the psychiatric ward")

    def test_outbound_guard_passes_clean_text(self):
        memory.assert_no_sensitive_leak("Want to see a Marvel movie Saturday?")


class TestCorrectionAndDeletion:
    def test_correction_supersedes_and_changes_retrieval(self):
        m = memory.store_memory(SAM, "preference", "Sam loves crowded theaters",
                                visibility="agent_internal")
        new = memory.correct_memory(m.id, "Sam avoids crowded theaters")

        values = [x.value for x in memory.list_memories(SAM)]
        assert "Sam avoids crowded theaters" in values
        assert "Sam loves crowded theaters" not in values
        assert new.confirmed is True

    def test_correction_keeps_a_provenance_trail(self):
        m = memory.store_memory(SAM, "fact", "original", visibility="agent_internal")
        new = memory.correct_memory(m.id, "corrected")
        assert "original" in (new.source_quote or "")

    def test_deletion_stops_retrieval_immediately(self):
        m = memory.store_memory(SAM, "fact", "Sam plays the trumpet",
                                visibility="agent_internal")
        assert any("trumpet" in x.value for x in memory.retrieve(SAM, "trumpet"))

        assert memory.delete_memory(m.id) is True
        assert memory.get_memory(m.id) is None
        assert not any("trumpet" in x.value for x in memory.retrieve(SAM, "trumpet"))

    def test_deleting_unknown_memory_is_false(self):
        assert memory.delete_memory("mem_does_not_exist") is False

    def test_visibility_change_moves_it_between_audiences(self):
        m = memory.store_memory(SAM, "fact", "Sam bikes to work",
                                visibility="private_to_sam")
        ctx = memory.shareable_context(SAM, JOSH, [])
        assert not any("bikes" in s for s in ctx["shared_interests"])

        memory.set_visibility(m.id, "shareable_with_contact")
        ctx2 = memory.shareable_context(SAM, JOSH, [])
        assert any("bikes" in s for s in ctx2["shared_interests"])

    def test_invalid_kind_and_visibility_rejected(self):
        with pytest.raises(memory.MemoryError_):
            memory.store_memory(SAM, "nonsense", "x")
        with pytest.raises(memory.MemoryError_):
            memory.store_memory(SAM, "fact", "x", visibility="world_readable")
