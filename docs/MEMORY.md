# Memory System

Source of truth: `backend/app/services/memory.py`, `backend/app/services/companion.py`,
`backend/app/adapters/anthropic_adapter.py`. Tests: `backend/tests/test_memory.py`.

## Pipeline

```
companion message
  -> save_message (persist user turn, conversations table)
  -> retrieve(audience="owner", limit=8)        memories the companion itself may use
  -> companion_reply()                           Claude, or deterministic fallback
  -> save_message (persist assistant turn)
  -> extract_memories()                          Claude, or deterministic fallback
       for each candidate:
         -> classify_sensitivity(value)           default visibility
         -> store_memory(..., source_quote, confidence, visibility)
  -> emit memory.retrieved / memory.extracted events
```

Implemented in `services/companion.handle_message()` (`companion.py:30-118`).
Extraction never invents and never infers clinical state — the extraction system
prompt is explicit: *"Only what the user actually said. Never infer a diagnosis, a
mental state, a risk level, or anything clinical. Never invent."*
(`adapters/anthropic_adapter.py:160-175`), and the fallback extractor
(`:226-277`) only pattern-matches sentences the user actually wrote (relationship
words, negation, "used to"/"we always"/"miss" phrases) — it has no path to assert a
clinical fact on its own.

## Provenance

Every `Memory` carries `source_conversation_id`, `source_quote`, and `confidence`
(`memory.py:44-61`). `store_memory()` requires a `kind` in
`{fact, preference, relationship, boundary}` and a known `visibility`, or raises
`MemoryError_` (`:89-118`). Corrections append a provenance trail:
`correct_memory()` stores the new value with
`source_quote=f"corrected from: {old['value']!r}"` (`:148-156`), so you can always
see what a fact used to say.

## Visibility ladder (narrowest to widest)

| value | meaning | numeric order |
|---|---|---|
| `private_to_sam` | never leaves the owner's own view | 0 |
| `agent_internal` | usable for planning, never spoken to a third party | 1 |
| `shareable_with_contact` | may be referenced on a call with an opted-in contact | 2 |
| `public_demo_safe` | safe for the judge-facing dashboard | 3 |

Defined in `memory.py:22-27` (`VISIBILITY_ORDER`). Default classification
(`classify_sensitivity()`, `:80-86`) is privacy-erring: anything matching the
sensitive-pattern regex becomes `private_to_sam`; everything else defaults to
`agent_internal`. Nothing defaults to `shareable_with_contact` or
`public_demo_safe` — those require an explicit widening (onboarding preferences
are the one exception, stored directly as `shareable_with_contact` because the
user is actively telling the companion what they're comfortable sharing,
`api/onboarding.py:190-195`).

## AUDIENCE_VISIBILITY allow-lists

`memory.py:200-209`. Each audience is an explicit *set* of allowed visibility
values, not an ordinal threshold — the code comment explains why this matters:
*"Being a set rather than an ordinal threshold means 'private_to_sam can reach
planning' is impossible to express by accident."*

| audience | allowed visibilities |
|---|---|
| `owner` | all four |
| `planning` | `agent_internal`, `shareable_with_contact`, `public_demo_safe` (private clinical context deliberately excluded) |
| `contact` | `shareable_with_contact`, `public_demo_safe` |
| `demo` | `public_demo_safe` only |

`retrieve(audience=...)` looks up `AUDIENCE_VISIBILITY.get(audience,
AUDIENCE_VISIBILITY["demo"])` (`:225`) — an unrecognized audience string fails
closed to the most restrictive set, not the most permissive
(`test_memory.py::test_unknown_audience_fails_closed`).

## The two independent contact-facing guards

`shareable_context()` (`memory.py:239-307`) is the **only** function the outbound
voice path (and the `get_support_context` tool) may use to build what a contact
can hear (`services/voice.py:102-105`, `agents/tools.py:171-173`,
`api/voice.py:102`). It applies two guards that do not trust each other:

1. **Visibility guard**: only rows with `visibility in {shareable_with_contact,
   public_demo_safe}` are even considered (`:254-263`). Everything else is
   recorded in the response as `withheld_by_visibility` for provenance.
2. **Content guard**: every candidate row is additionally checked against
   `_SENSITIVE_RE` (`:31-37`, matching `diagnos*`, `hospitaliz*`, `discharg*`,
   `psychiatric`, `depress*`, `suicid*`, `self-harm`, `medication`,
   `antidepress*`, `therapist`, `inpatient`, `ward`, `crisis`, `meds`). A row that
   is labeled `shareable_with_contact` but matches this pattern is still blocked
   and counted as `blocked_by_content_guard` (`:265-280`). This is defense in
   depth against a mislabelled row — proven directly by
   `test_memory.py::test_mislabelled_private_row_is_still_blocked`.

There is a third, final check: `assert_no_sensitive_leak()` (`:310-316`) raises
`MemoryError_` if any text about to be sent outbound (dynamic variables to
Cartesia, the confirmation SMS text) matches the same sensitive pattern —
`services/voice.py:122-125` calls this on every dynamic variable before dialing,
and `services/plans.send_confirmation()` calls it on the confirmation message
before marking the plan `NOTIFICATIONS_SENT` (`plans.py:330-331`).

## Corrections and supersession

`correct_memory()` (`memory.py:134-164`): by default (`supersede=True`) the old
row gets `superseded_by = <new id>` and an updated `updated_at`, and
`list_memories()` filters out superseded rows (`:126-131`, `AND superseded_by IS
NULL`). Retrieval therefore returns the correction, never the stale value, while
the old row is kept in the table as a tombstone with full history. Verified by
`test_memory.py::TestCorrectionAndDeletion`.

`set_visibility()` lets a user widen or narrow a memory's audience without
changing its value (`:167-177`) — used when a memory moves between
`AUDIENCE_VISIBILITY` buckets (e.g. a fact marked private is later shared).

## Deletion tombstones

`delete_memory()` (`memory.py:180-188`) sets `deleted_at`, never issues a SQL
`DELETE`. `get_memory()` and `list_memories()` both filter on `deleted_at IS NULL`
by default, so a deleted memory stops being retrievable immediately
(`test_memory.py::test_deletion_stops_retrieval_immediately`) while the row itself
survives for audit.

## Correction/deletion changing planning

The connection planner (`agents/orchestrator.py`) reads memory through
`memory.retrieve(audience="planning")` — the same retrieval path everything else
uses. `choose_activity()` (`orchestrator.py:118-168`) explicitly treats a
`boundary`-kind memory containing crowd-avoidance language as a veto on crowded
activities (`_AVOID_CROWDS` regex, `:59-61`, `:152-156`). This is exercised
end-to-end in `scripts/e2e_demo.py` step 12: after the companion extracts a new
boundary ("I don't want crowded theaters anymore"), the next planning pass no
longer proposes "a Marvel movie" — proving corrections change downstream behavior,
not just the memory panel.

## API surface

`GET /api/memories?user_id=&audience=owner` (`api/companion.py:23-33`),
`PATCH /api/memories/{id}` (`:41-73`, value change → correction + new row;
visibility-only change → in-place), `DELETE /api/memories/{id}` (`:76-88`). All
three are backed directly by the functions above with no additional business
logic in the router layer.
