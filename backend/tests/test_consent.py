"""Consent gating: the property that outreach cannot happen without dual opt-in."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.db import ex
from app.services import consent as cs
from app.services.demo_reset import DANIEL, JOSH, MAYA, SAM


class TestE164:
    @pytest.mark.parametrize("good", ["+14155551234", "+447911123456", "+8613800138000"])
    def test_accepts_valid(self, good):
        assert cs.validate_e164(good) == good

    @pytest.mark.parametrize(
        "bad", ["4155551234", "+0415555123", "", "not a phone", "+1", "++14155551234"]
    )
    def test_rejects_invalid(self, bad):
        with pytest.raises(cs.ConsentError):
            cs.validate_e164(bad)

    def test_normalises_spacing(self):
        assert cs.validate_e164(" +1 415-555-1234 ") == "+14155551234"


class TestOutreachGate:
    def test_accepted_and_verified_contact_is_allowed(self):
        d = cs.verify_outreach(SAM, JOSH, "voice_call", override_quiet_hours=True)
        assert d.allowed, d.reason

    def test_contact_who_never_accepted_is_blocked(self):
        d = cs.verify_outreach(SAM, DANIEL, "voice_call", override_quiet_hours=True)
        assert not d.allowed
        assert "has not accepted" in d.reason

    def test_action_outside_consented_scope_is_blocked(self):
        """Maya consented to SMS only."""
        d = cs.verify_outreach(SAM, MAYA, "voice_call", override_quiet_hours=True)
        assert not d.allowed
        assert "not in consented actions" in d.reason

        d2 = cs.verify_outreach(SAM, MAYA, "sms", override_quiet_hours=True)
        assert d2.allowed, d2.reason

    def test_unverified_phone_is_blocked(self):
        ex("UPDATE users SET phone_verified=0 WHERE id=?", (JOSH,))
        d = cs.verify_outreach(SAM, JOSH, "voice_call", override_quiet_hours=True)
        assert not d.allowed
        assert "not verified" in d.reason

    def test_inactive_outreach_blocks_everything(self):
        ex("UPDATE users SET outreach_active=0 WHERE id=?", (SAM,))
        d = cs.verify_outreach(SAM, JOSH, "voice_call", override_quiet_hours=True)
        assert not d.allowed
        assert "not activated outreach" in d.reason

    def test_revocation_blocks_immediately(self):
        assert cs.verify_outreach(SAM, JOSH, "voice_call", override_quiet_hours=True).allowed
        cs.revoke_consent(SAM, JOSH)
        d = cs.verify_outreach(SAM, JOSH, "voice_call", override_quiet_hours=True)
        assert not d.allowed
        assert "no active consent" in d.reason

    def test_rate_limit_blocks_after_cap(self):
        ex("UPDATE consents SET max_per_day=2 WHERE patient_id=? AND contact_id=?",
           (SAM, JOSH))
        for _ in range(2):
            cs.record_outreach(SAM, JOSH, "voice_call")
        d = cs.verify_outreach(SAM, JOSH, "voice_call", override_quiet_hours=True)
        assert not d.allowed
        assert "frequency cap" in d.reason

    def test_quiet_hours_block_when_not_overridden(self):
        # Force a window that definitely contains "now".
        now_h = datetime.now(timezone.utc).hour
        ex("UPDATE consents SET quiet_hours_start=?, quiet_hours_end=?"
           " WHERE patient_id=? AND contact_id=?",
           (now_h, (now_h + 2) % 24, SAM, JOSH))
        d = cs.verify_outreach(SAM, JOSH, "voice_call")
        assert not d.allowed
        assert "quiet hours" in d.reason

    def test_unknown_users_are_blocked(self):
        assert not cs.verify_outreach("nobody", JOSH, "voice_call").allowed
        assert not cs.verify_outreach(SAM, "nobody", "voice_call").allowed


class TestQuietHourWrap:
    @pytest.mark.parametrize(
        "hour,start,end,expected",
        [
            (22, 21, 9, True),    # inside wrapped window
            (3, 21, 9, True),     # inside wrapped window, after midnight
            (12, 21, 9, False),   # outside
            (9, 21, 9, False),    # boundary is exclusive at end
            (21, 21, 9, True),    # boundary is inclusive at start
            (10, 9, 17, True),    # non-wrapped window
            (18, 9, 17, False),   # non-wrapped, outside
        ],
    )
    def test_wrap(self, hour, start, end, expected):
        assert cs._in_quiet_hours(hour, start, end) is expected


class TestStop:
    def test_stop_revokes_everything_for_that_number(self, client):
        from app.db import q1

        phone = q1("SELECT phone_e164 FROM users WHERE id=?", (JOSH,))["phone_e164"]
        result = cs.handle_stop(phone)
        assert result["ok"]
        assert result["consents_revoked"] >= 1
        assert not cs.verify_outreach(SAM, JOSH, "voice_call",
                                      override_quiet_hours=True).allowed

    def test_stop_for_unknown_number_is_safe(self):
        assert cs.handle_stop("+14155559999")["ok"] is False
