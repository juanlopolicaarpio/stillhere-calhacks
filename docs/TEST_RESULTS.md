# docs/TEST_RESULTS.md

Actual recorded test runs. Commands and output are verbatim, not summarised.

- **Recorded:** 2026-10-24 (UTC)
- **Platform:** Windows 11, Python 3.14.2, Node 24.12.0
- **Environment:** no provider credentials present. `CARTESIA_API_KEY`,
  `ANTHROPIC_API_KEY` and Twilio variables were all unset, so the suite exercises the
  deterministic fallback and simulator paths. `conftest.py` actively deletes these
  variables so a test run can never touch a live provider or place a real call.

---

## 1. Unit + integration suite

```
$ python -m pytest backend/tests -q
........................................................................ [ 63%]
.........................................                                [100%]
113 passed in 14.76s
```

**113 passed, 0 failed.**

## 2. End-to-end demo run

Exercises the full coordination loop in-process against the real FastAPI app using the
scripted voice adapter, asserting real state transitions and the negative safety
properties.

```
$ python scripts/e2e_demo.py
...
== 15. webhook tool auth ==
  PASS  tool endpoint rejects missing secret

====================================================================
RESULT: 49 passed, 0 failed
====================================================================
```

**49 checks passed, 0 failed.** The run covers, in order: health and capability flags,
fictional seed, memory provenance, contact-view privacy filtering, the consent gate
(un-accepted contact, SMS-only consent, verified contact), memory-driven selection of
contact and activity with citations, refusal to confirm before approvals, the live
conversation branches ("who is this?", "how is Sam doing?", "just book it"), mismatched
approval refusal, matching simulated approval and confirmation, idempotency, event
vocabulary and labeling, memory correction changing the plan, consent revocation halting
an in-flight plan, the STOP keyword, and webhook tool authentication.

## 3. Verified dependency versions

| package | version |
|---|---|
| fastapi | 0.162.0 |
| starlette | 1.9.0 |
| pydantic | 2.14.1 |
| uvicorn | 0.54.0 |
| httpx | 0.28.1 |
| sse-starlette | 3.5.0 |
| qrcode | 8.2 |
| pytest | 9.1.1 |
| cartesia | 4.2.0 |
| anthropic | 2.3.0 |

## 4. Live HTTP verification

Checked against a running `uvicorn` instance on port 8000:

| check | result |
|---|---|
| `GET /health` | `{"status":"ok","db":"ok","voice_mode":"simulated"}` |
| `GET /api/status` | `real_call_possible: false`, honest `notes` string |
| `GET /api/qr.png` | HTTP 200, `image/png`, 761 bytes |
| `POST /api/demo/reset` | 7 memories seeded, reset_version increments |
| `POST /api/demo/start` | selected Josh Tanaka + "a Marvel movie", 4 memory citations |
| `POST /api/voice/simulate/turn` ("Sunday instead?") | re-planned, version bumped, approvals cleared |
| `POST /api/demo/sam-response` | confirmed on the new agreed time |

## 5. What is NOT tested

Stated plainly, because these are the gaps that matter:

| area | status | why |
|---|---|---|
| Real outbound PSTN call | **not run** | no `CARTESIA_API_KEY` was supplied. `scripts/smoke_call.py` is written and exits 2 with a clear message rather than pretending. |
| Live Cartesia webhook tool delivery | **not run** | requires a publicly reachable `PUBLIC_BASE_URL` and a configured Managed Agent. |
| Live Claude API responses | **not run** | no `ANTHROPIC_API_KEY`. The adapter targets `messages.create` on anthropic 2.3.0; only the fallback path is covered by tests. |
| Real SMS delivery | **not run** | Twilio not configured. Verification codes are returned as `dev_code` for local testing and confirmations emit `simulated` events. |
| Voice latency / interruption recovery on a real call | **not measured** | no call has been placed. See docs/VOICE_EVALUATION.md. |
| Frontend browser E2E (Playwright etc.) | **not written** | frontend verified by `npm run build` (zero TypeScript errors) and manual checks against the live API. |

## 6. Reproducing

```bash
make test       # pytest suite
make e2e        # end-to-end run
make preflight  # Cartesia preflight (exits 2 without a key)
make call       # real call attempt (exits 2 without a key)
```

---

## Appendix — full test inventory


### `backend/tests/test_api_flow.py` — 45 tests

- `TestHealthAndStatus::test_health`
- `TestHealthAndStatus::test_status_reports_simulated_without_keys`
- `TestHealthAndStatus::test_status_leaks_no_secrets`
- `TestOnboarding::test_signup_requires_adult_attestation`
- `TestOnboarding::test_signup_rejects_bad_phone`
- `TestOnboarding::test_full_signup_and_verify`
- `TestOnboarding::test_wrong_code_is_rejected`
- `TestOnboarding::test_cannot_activate_outreach_before_verifying`
- `TestInvitationsAndDualConsent::test_invite_page_leaks_no_clinical_context`
- `TestInvitationsAndDualConsent::test_accept_records_optin_but_not_phone_verification`
- `TestInvitationsAndDualConsent::test_unverified_contact_cannot_be_called`
- `TestInvitationsAndDualConsent::test_accept_with_code_verifies_and_unblocks`
- `TestInvitationsAndDualConsent::test_double_accept_is_rejected`
- `TestInvitationsAndDualConsent::test_decline_blocks_outreach`
- `TestInvitationsAndDualConsent::test_unknown_token_404s`
- `TestInvitationsAndDualConsent::test_circle_lists_seeded_and_new_invites`
- `TestInvitationsAndDualConsent::test_contacts_resolve_names`
- `TestInvitationsAndDualConsent::test_consent_scope_cannot_be_set_before_acceptance`
- `TestInvitationsAndDualConsent::test_consents_include_contact_names`
- `TestCompanionAndMemoryApi::test_message_persists_and_extracts`
- `TestCompanionAndMemoryApi::test_memories_respect_audience`
- `TestCompanionAndMemoryApi::test_unknown_audience_rejected`
- `TestCompanionAndMemoryApi::test_patch_and_delete`
- `TestCompanionAndMemoryApi::test_patch_with_no_fields_rejected`
- `TestVoiceTools::test_tools_require_secret`
- `TestVoiceTools::test_tools_reject_wrong_secret`
- `TestVoiceTools::test_support_context_is_contact_safe`
- `TestVoiceTools::test_showtimes_are_labeled_mock`
- `TestVoiceTools::test_record_response_is_idempotent`
- `TestVoiceTools::test_agent_cannot_confirm_via_tools`
- `TestVoiceTools::test_webhook_unknown_call_does_not_crash`
- `TestVoiceTools::test_webhook_maps_status`
- `TestDemoFlowApi::test_reset_is_repeatable`
- `TestDemoFlowApi::test_sam_response_rejected_when_not_pending`
- `TestDemoFlowApi::test_sam_decline_ends_without_confirming`
- `TestDemoFlowApi::test_friend_decline_ends_without_confirming`
- `TestDemoFlowApi::test_counterproposal_then_accept_on_same_call`
- `TestDemoFlowApi::test_counterproposal_clears_prior_approvals`
- `TestDemoFlowApi::test_coffee_counterproposal_then_accept`
- `TestDemoFlowApi::test_plan_exposes_structured_rationale`
- `TestDemoFlowApi::test_simulate_turn_rejected_for_real_calls`
- `TestDashboardAuth::test_events_require_token`
- `TestDashboardAuth::test_header_token_works`
- `TestDashboardAuth::test_query_token_works_for_eventsource`
- `TestDashboardAuth::test_bad_token_rejected`

### `backend/tests/test_consent.py` — 28 tests

- `TestE164::test_accepts_valid[+14155551234]`
- `TestE164::test_accepts_valid[+447911123456]`
- `TestE164::test_accepts_valid[+8613800138000]`
- `TestE164::test_rejects_invalid[4155551234]`
- `TestE164::test_rejects_invalid[+0415555123]`
- `TestE164::test_rejects_invalid[]`
- `TestE164::test_rejects_invalid[not a phone]`
- `TestE164::test_rejects_invalid[+1]`
- `TestE164::test_rejects_invalid[++14155551234]`
- `TestE164::test_normalises_spacing`
- `TestOutreachGate::test_accepted_and_verified_contact_is_allowed`
- `TestOutreachGate::test_contact_who_never_accepted_is_blocked`
- `TestOutreachGate::test_action_outside_consented_scope_is_blocked`
- `TestOutreachGate::test_unverified_phone_is_blocked`
- `TestOutreachGate::test_inactive_outreach_blocks_everything`
- `TestOutreachGate::test_revocation_blocks_immediately`
- `TestOutreachGate::test_rate_limit_blocks_after_cap`
- `TestOutreachGate::test_quiet_hours_block_when_not_overridden`
- `TestOutreachGate::test_unknown_users_are_blocked`
- `TestQuietHourWrap::test_wrap[22-21-9-True]`
- `TestQuietHourWrap::test_wrap[3-21-9-True]`
- `TestQuietHourWrap::test_wrap[12-21-9-False]`
- `TestQuietHourWrap::test_wrap[9-21-9-False]`
- `TestQuietHourWrap::test_wrap[21-21-9-True]`
- `TestQuietHourWrap::test_wrap[10-9-17-True]`
- `TestQuietHourWrap::test_wrap[18-9-17-False]`
- `TestStop::test_stop_revokes_everything_for_that_number`
- `TestStop::test_stop_for_unknown_number_is_safe`

### `backend/tests/test_memory.py` — 25 tests

- `TestPersistence::test_survives_a_simulated_restart`
- `TestPersistence::test_retrieval_finds_it_by_query`
- `TestProvenance::test_seeded_memories_all_cite_a_source`
- `TestProvenance::test_source_conversation_is_linked`
- `TestSensitivityClassification::test_clinical_text_is_private_by_default[Sam was discharged from the hospital]`
- `TestSensitivityClassification::test_clinical_text_is_private_by_default[Sam has a depression diagnosis]`
- `TestSensitivityClassification::test_clinical_text_is_private_by_default[Sam takes medication daily]`
- `TestSensitivityClassification::test_clinical_text_is_private_by_default[Sam saw his therapist]`
- `TestSensitivityClassification::test_clinical_text_is_private_by_default[Sam was an inpatient]`
- `TestSensitivityClassification::test_ordinary_text_is_internal_by_default[Sam likes Marvel movies]`
- `TestSensitivityClassification::test_ordinary_text_is_internal_by_default[Josh is Sam's best friend]`
- `TestSensitivityClassification::test_ordinary_text_is_internal_by_default[Sam prefers plans made in advance]`
- `TestPrivacyFiltering::test_shareable_context_excludes_private_clinical_memory`
- `TestPrivacyFiltering::test_shareable_context_still_has_useful_content`
- `TestPrivacyFiltering::test_mislabelled_private_row_is_still_blocked`
- `TestPrivacyFiltering::test_audience_allow_lists_are_disjoint_where_it_matters`
- `TestPrivacyFiltering::test_unknown_audience_fails_closed`
- `TestPrivacyFiltering::test_outbound_guard_raises_on_sensitive_text`
- `TestPrivacyFiltering::test_outbound_guard_passes_clean_text`
- `TestCorrectionAndDeletion::test_correction_supersedes_and_changes_retrieval`
- `TestCorrectionAndDeletion::test_correction_keeps_a_provenance_trail`
- `TestCorrectionAndDeletion::test_deletion_stops_retrieval_immediately`
- `TestCorrectionAndDeletion::test_deleting_unknown_memory_is_false`
- `TestCorrectionAndDeletion::test_visibility_change_moves_it_between_audiences`
- `TestCorrectionAndDeletion::test_invalid_kind_and_visibility_rejected`

### `backend/tests/test_state_machine.py` — 15 tests

- `TestTransitions::test_happy_path_is_legal`
- `TestTransitions::test_cannot_jump_straight_to_confirmed`
- `TestTransitions::test_cannot_confirm_from_friend_accepted`
- `TestTransitions::test_counterproposal_loops_and_never_confirms`
- `TestTransitions::test_revocation_reachable_from_every_live_state`
- `TestTransitions::test_terminal_states_are_terminal`
- `TestConfirmationGate::test_confirms_when_both_accepted_identical_terms`
- `TestConfirmationGate::test_refuses_without_patient`
- `TestConfirmationGate::test_refuses_without_contact`
- `TestConfirmationGate::test_refuses_on_time_mismatch`
- `TestConfirmationGate::test_refuses_on_activity_mismatch`
- `TestConfirmationGate::test_refuses_when_either_declined`
- `TestConfirmationGate::test_refuses_when_counterproposed`
- `TestConfirmationGate::test_refuses_before_availability_revalidated`
- `TestConfirmationGate::test_refuses_after_revocation`
