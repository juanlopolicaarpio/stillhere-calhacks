"""Central configuration. Loads .env from repo root. No secrets are ever logged."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")


def _clean(v: str | None) -> str:
    """Strip whitespace and accidental surrounding quotes from env values."""
    if v is None:
        return ""
    return v.strip().strip('"').strip("'")


class Settings:
    # --- Cartesia (P0 real PSTN) ---
    cartesia_api_key: str = _clean(os.getenv("CARTESIA_API_KEY"))
    cartesia_version: str = _clean(os.getenv("CARTESIA_VERSION")) or "2026-08-14"
    cartesia_agent_id: str = _clean(os.getenv("CARTESIA_AGENT_ID"))
    cartesia_from_number_id: str = _clean(os.getenv("CARTESIA_FROM_NUMBER_ID"))
    cartesia_voice_id: str = _clean(os.getenv("CARTESIA_VOICE_ID")) or "a0e99841-438c-4a64-b679-ae501e7d6091"
    cartesia_tts_model: str = _clean(os.getenv("CARTESIA_TTS_MODEL")) or "sonic-3.6"
    cartesia_base_url: str = "https://api.cartesia.ai"

    # --- Anthropic ---
    anthropic_api_key: str = _clean(os.getenv("ANTHROPIC_API_KEY"))
    anthropic_model: str = _clean(os.getenv("ANTHROPIC_MODEL")) or "claude-opus-5"

    # --- Demo target ---
    my_phone_e164: str = _clean(os.getenv("MY_PHONE_E164"))

    # --- Web ---
    public_base_url: str = _clean(os.getenv("PUBLIC_BASE_URL")) or "http://localhost:8000"
    webhook_secret: str = _clean(os.getenv("WEBHOOK_SECRET")) or "dev-only-change-me"
    dashboard_token: str = _clean(os.getenv("DASHBOARD_TOKEN")) or "dev-dashboard-token"

    # --- Twilio (optional) ---
    twilio_account_sid: str = _clean(os.getenv("TWILIO_ACCOUNT_SID"))
    twilio_auth_token: str = _clean(os.getenv("TWILIO_AUTH_TOKEN"))
    twilio_from_number: str = _clean(os.getenv("TWILIO_FROM_NUMBER"))

    db_path: Path = Path(_clean(os.getenv("DB_PATH")) or (REPO_ROOT / "stillhere.db"))

    # --- Safety rails (server-enforced, never model-controlled) ---
    quiet_hours_start: int = 21  # 21:00 local
    quiet_hours_end: int = 9     # 09:00 local
    max_calls_per_contact_per_day: int = 2

    # --- Capability flags: the single source of truth for "is this real?" ---
    @property
    def cartesia_configured(self) -> bool:
        return bool(self.cartesia_api_key)

    @property
    def cartesia_call_ready(self) -> bool:
        """True only when a REAL outbound PSTN call is actually possible."""
        return bool(self.cartesia_api_key and self.cartesia_agent_id and self.cartesia_from_number_id)

    @property
    def anthropic_configured(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def twilio_configured(self) -> bool:
        return bool(self.twilio_account_sid and self.twilio_auth_token)

    def voice_mode(self) -> str:
        """'cartesia_pstn' = real phone call. 'simulated' = scripted local adapter."""
        return "cartesia_pstn" if self.cartesia_call_ready else "simulated"

    def redacted(self) -> dict:
        """Safe-to-log capability snapshot. Never contains secret material."""
        return {
            "cartesia_configured": self.cartesia_configured,
            "cartesia_call_ready": self.cartesia_call_ready,
            "cartesia_version": self.cartesia_version,
            "cartesia_agent_id_present": bool(self.cartesia_agent_id),
            "cartesia_from_number_id_present": bool(self.cartesia_from_number_id),
            "anthropic_configured": self.anthropic_configured,
            "anthropic_model": self.anthropic_model if self.anthropic_configured else None,
            "twilio_configured": self.twilio_configured,
            "voice_mode": self.voice_mode(),
            "target_phone_present": bool(self.my_phone_e164),
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
