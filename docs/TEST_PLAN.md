# Test Plan

Two independent suites, both exercised against the real FastAPI app with no
mocking of our own code. Both verified to pass by actually running them on
2026-10-24.

```
make test   # python -m pytest backend/tests -q   -> 113 passed
make e2e    # python scripts/e2e_demo.py           -> 49 passed, 0 failed
```

## Suite 1: `backend/tests/` (pytest, 113 tests)

Isolated per-session temp SQLite DB (`backend/tests/conftest.py:13-21`); a
`fresh_db` autouse fixture wipes and reseeds before every test
(`:24-42`); provider API keys are explicitly stripped from the environment
before tests run (`:18-21`) so the suite can never accidentally hit a real
provider.

| file | what it proves | representative tests |
|---|---|---|
| `test_state_machine.py` | the deterministic core: illegal transitions rejected, the confirmation gate requires identical terms from both parties | `TestTransitions::test_cannot_confirm_from_friend_accepted` ("The LLM's most likely shortcut must be impossible"), `TestConfirmationGate::test_refuses_on_time_mismatch` ("Josh said Sunday, Sam approved Saturday → must NOT confirm") |
| `test_consent.py` | the outreach gate: E.164 validation, dual opt-in, verified phone, consent scope, quiet hours (incl. midnight wrap), rate cap, revocation, STOP | `TestOutreachGate::test_contact_who_never_accepted_is_blocked`, `test_unverified_phone_is_blocked`, `test_rate_limit_blocks_after_cap`, `TestQuietHourWrap` (7 wrap-around cases), `TestStop::test_stop_revokes_everything_for_that_number` |
| `test_memory.py` | persistence across a simulated restart, provenance, sensitivity classification, the two independent contact-facing guards, corrections/deletion, audience allow-lists | `TestPersistence::test_survives_a_simulated_restart`, `TestPrivacyFiltering::test_mislabelled_private_row_is_still_blocked`, `TestCorrectionAndDeletion::test_correction_supersedes_and_changes_retrieval`, `test_audience_allow_lists_are_disjoint_where_it_matters` |
| `test_api_flow.py` | end-to-end HTTP behavior: onboarding, dual-consent invites, voice tool auth + idempotency, demo flow (counterproposals, declines, structured rationale), dashboard token auth | `TestInvitationsAndDualConsent::test_accept_records_optin_but_not_phone_verification`, `TestVoiceTools::test_agent_cannot_confirm_via_tools`, `TestDemoFlowApi::test_counterproposal_then_accept_on_same_call`, `TestDashboardAuth::test_query_token_works_for_eventsource` |

### Running a subset

```
./.venv/Scripts/python.exe -m pytest backend/tests/test_consent.py -q
./.venv/Scripts/python.exe -m pytest backend/tests -k "quiet_hours" -q
```

## Suite 2: `scripts/e2e_demo.py` (49 checks, in-process TestClient)

A single linear script that runs the entire coordination loop against the real
app and asserts both the happy path and the safety properties, printing a
PASS/FAIL line per check. Exits non-zero on any failure (`check()`,
`e2e_demo.py:27-33`; `_finish()`, `:234-242`).

| step | what it exercises |
|---|---|
| 0–1 | health, capability status, demo reset |
| 2–3 | memory persistence/provenance, and that contact-facing memory excludes clinical terms |
| 4 | the consent gate: Daniel (never accepted) blocked, Maya (SMS-only) blocked for voice, Josh (accepted+verified) allowed |
| 5 | the planner picks Josh and "a Marvel movie" **from stored memory**, not a hardcoded value, and cites real memory ids |
| 6 | a premature confirm attempt (before any approvals) is refused |
| 7 | a live simulated conversation: the agent discloses it is an AI when asked, refuses to disclose "how is Sam doing", refuses "just book it, don't ask Sam", then accepts a "yes" and halts at `PATIENT_APPROVAL_PENDING` |
| 8 | a mismatched-terms approval (wrong `starts_at`) is refused, not confirmed |
| 9 | a matching simulated Sam approval **does** confirm, flagged `simulated: true`, and the workflow reaches `COMPLETE` |
| 10 | idempotent replay of `confirm_plan` returns identical output; re-approving a completed plan is rejected |
| 11 | every expected event type was actually emitted (`demo.reset`, `consent.verified`, `memory.retrieved`, `plan.candidate_created`, `call.requested`, `friend.responded`, `plan.confirmed`, `notification.sent`, `safety.blocked`), simulated events are flagged, and no raw phone number appears in any event payload |
| 12 | a stated boundary ("I don't want crowded theaters") is extracted as a memory and **changes the next planning pass** — the theater activity is suppressed |
| 13 | revoking consent halts the in-flight plan (`HALTED_CONSENT_REVOKED`) and blocks any new outreach attempt |
| 14 | the STOP keyword revokes consent by phone number |
| 15 | voice tool endpoints reject requests with no webhook secret |

### Running it

```
./.venv/Scripts/python.exe scripts/e2e_demo.py
```

Output ends with `RESULT: 49 passed, 0 failed` on success and lists every
failure explicitly if not.

## What is deliberately NOT tested

- Real Cartesia PSTN calls — no API key is configured; `scripts/smoke_call.py`
  and `scripts/discover_cartesia.py` exist for that purpose but require a real
  key and are not part of either automated suite.
- Real Anthropic Claude responses — no API key is configured; the fallback
  path is what both suites actually exercise (`test_api_flow.py:176`,
  `assert r["reasoning_mode"] == "fallback"`, explicit about this).
- Frontend component/unit tests — no test runner is configured in
  `frontend/package.json` beyond `tsc -b` type-checking at build time.
- Load/concurrency testing — SQLite with one connection per thread
  (`db.py:216-226`) has not been stress-tested under concurrent writers.
