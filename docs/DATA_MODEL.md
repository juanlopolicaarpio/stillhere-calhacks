# Data Model

Source of truth: `backend/app/db.py:15-204` (`SCHEMA`). SQLite, one file
(`stillhere.db` by default, overridable via `DB_PATH`), WAL journal mode, foreign
keys on. 15 tables.

## 1. `users`

Both patients and contacts are rows here, distinguished by `role`.

| column | type | meaning |
|---|---|---|
| `id` | TEXT PK | e.g. `user_sam`, or `user_<hex12>` for signups |
| `display_name` | TEXT | |
| `role` | TEXT | `patient` or `contact` |
| `phone_e164` | TEXT | |
| `phone_verified` | INTEGER (bool) | set only by a consumed verification code — never by holding an invite link |
| `timezone` | TEXT | default `America/Los_Angeles` |
| `preferred_channel` | TEXT | `call` or `text` |
| `is_adult_attested` | INTEGER (bool) | required `true` at signup |
| `outreach_active` | INTEGER (bool) | the patient's own master switch; irrelevant for contacts |
| `created_at` | TEXT (ISO) | |

## 2. `verification_codes`

| column | meaning |
|---|---|
| `phone_e164`, `code`, `purpose` (`signup`/`invite`) | |
| `expires_at` | 10-minute TTL from issuance |
| `consumed_at` | set once used; codes are single-use |
| `attempts` | capped at 5 (`MAX_CODE_ATTEMPTS`) |

## 3. `relationships`

One row per (patient, contact) pair. `status` is `invited` → `accepted` or
`declined` (or `revoked` via STOP). This is the record of the contact's own
affirmative opt-in — distinct from `consents`, which records the *scope* of what
they agreed to.

## 4. `invitations`

The pending-offer stage before a `relationships` row exists. Keyed by a random
URL-safe `token`. `accepted_at`/`declined_at` are mutually resolved with decline
taking precedence if both are somehow set. `contact_id` is filled in once accepted.

## 5. `conversations`

Every companion chat turn (`message_role`: `user`/`assistant`), tagged
`sensitivity` (`normal`/`sensitive`) and linked to a `session_id`. This is the
provenance source for extracted memories (`source_conversation_id`).

## 6. `memories`

The durable, provenance-tracked fact store. See `docs/MEMORY.md` for the full
model.

| column | meaning |
|---|---|
| `kind` | `fact`\|`preference`\|`relationship`\|`boundary` |
| `value` | the stored statement text |
| `source_conversation_id`, `source_quote` | provenance |
| `confidence` | 0.0–1.0 |
| `confirmed` | bool, true for user-stated/corrected facts |
| `visibility` | one of the four visibility-ladder values |
| `superseded_by` | set when a correction replaces this row; retrieval excludes superseded rows |
| `deleted_at` | tombstone; retrieval excludes deleted rows |

## 7. `consents`

The scope of what a contact agreed to, separate from the fact that they agreed.

| column | meaning |
|---|---|
| `allowed_actions` | JSON array, subset of `["voice_call","sms"]` |
| `allowed_fields` | JSON array, e.g. `["first_name","shared_interests","general_availability"]` — what the contact-safe context may include |
| `quiet_hours_start`/`end` | per-contact override of the global default (21:00–09:00) |
| `max_per_day` | rate cap, default 2 |
| `active` | flips to 0 on revocation; a new row is created rather than mutating an old one when scope changes |
| `revoked_at` | set on revocation |

## 8. `availability`

Free/busy windows per user (`start_at`, `end_at`), `source` (`seed` in the demo),
`visibility` always `agent_internal` — availability is planning-only, never
spoken to a contact directly.

## 9. `plans`

The central workflow object.

| column | meaning |
|---|---|
| `activity`, `location`, `starts_at` | proposed terms |
| `status` | a `PlanState` value (see `docs/API.md` / state machine) |
| `version` | incremented on every counterproposal; a new version clears both parties' approvals |
| `rationale` | combined human-readable "why" string |
| `why_contact`, `why_activity` | structured rationale fields, added after the frontend review flagged that a single opaque string wasn't enough for the dashboard (see `docs/MULTI_AGENT_ORCHESTRATION.md`) |
| `memory_citations` | JSON array of memory ids that justified this plan |
| `confirmed_at` | set only by `plans.confirm_plan()` |
| `idempotency_key` | the key that produced the confirmation, for idempotent replay |

## 10. `plan_participants`

Composite PK `(plan_id, user_id)`. One row per role (`patient`, `contact`).

| column | meaning |
|---|---|
| `response` | `accepted`\|`declined`\|`counterproposed`\|NULL |
| `responded_activity`, `responded_starts_at` | the **exact terms this party agreed to** — not just "yes". This is what lets `evaluate_confirmation()` detect a mismatch between what Josh accepted and what Sam approved |
| `simulated` | true when this response came from the demo simulator, not a real person |

## 11. `calls`

| column | meaning |
|---|---|
| `provider` | `cartesia` or `simulated` |
| `provider_call_id` | Cartesia's own call id, used to correlate webhook events |
| `to_number_redacted` | last 4 digits only (`***1234`) — the full number is never persisted in a form events could leak |
| `status` | `requested`→`ringing`→`answered`→`completed`/`failed`/`no_answer`/`busy`/`voicemail` |
| `mode` | `cartesia_pstn` or `simulated` — the honesty flag, carried on the row itself |
| `idempotency_key` | UNIQUE — prevents dialing the same person twice for one key |
| `raw_provider_response` | truncated JSON, for debugging a real call |

## 12. `agent_events`

The dashboard's entire data source. See `docs/DASHBOARD.md`.

| column | meaning |
|---|---|
| `event_type` | one of the fixed vocabulary strings |
| `safe_summary` | human-readable, redacted |
| `payload` | JSON, passed through `events.redact()` before storage — secrets/PII masked to last 4 chars |
| `simulated` | bool, read directly by the UI to render the `SIMULATED` badge |
| `seq` | monotonic integer, used for ordering and replay |

## 13. `outreach_log`

One row per actual outreach attempt (`patient_id`, `contact_id`, `channel`,
`occurred_at`). This is what the 24-hour rate cap counts against
(`consent.verify_outreach`, `services/consent.py:159-168`).

## 14. `idempotency`

Generic key-value replay cache, `scope` distinguishes callers (`confirm_plan`,
`send_confirmation`, `voice_tool`). Stores the full JSON result so a retried
request gets byte-identical output instead of re-executing side effects.

## 15. `demo_sessions`

One row per `demo_reset()` call, tracking `reset_version` (monotonically
increasing) and `state`. Lets the e2e script and UI confirm a reset actually
happened and which generation of seed data is current.

## Visibility ladder (cross-reference)

See `docs/MEMORY.md` for the full ladder and audience allow-lists. Summary:
`private_to_sam` (0) < `agent_internal` (1) < `shareable_with_contact` (2) <
`public_demo_safe` (3), stored as a plain string column on `memories.visibility`,
never an enforced SQL constraint — correctness is enforced in
`memory.py:VISIBILITY_ORDER` / `AUDIENCE_VISIBILITY` at the application layer.

## Indices

`idx_mem_owner (memories.owner_id, deleted_at)`,
`idx_conv_owner (conversations.owner_id, created_at)`,
`idx_events_seq (agent_events.seq)`,
`idx_calls_idem (calls.idempotency_key)` (`db.py:200-203`).

## Reset behavior

`wipe_all()` (`db.py:249-262`) deletes every application row (not the schema) in
foreign-key-safe order, with `PRAGMA foreign_keys=OFF` around the delete loop.
Used by `services/demo_reset.reset()` between rehearsals.
