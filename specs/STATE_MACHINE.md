# specs/STATE_MACHINE.md

Implemented in `backend/app/domain/state_machine.py`. Tested in
`backend/tests/test_state_machine.py` (29 tests). The code is authoritative.

## Why it is deterministic

The LLM may propose any next step. It cannot perform one. Every state change goes
through `plans.set_state()`, which calls `assert_transition()` and raises
`TransitionError` (HTTP 409 `illegal_transition`) on anything not explicitly allowed.
There is no code path where model output selects a terminal state.

## States

| state | meaning |
|---|---|
| `OPPORTUNITY_IDENTIFIED` | planner found a candidate pairing |
| `CONSENT_VERIFIED` | contact opted in, phone verified, all gates passed |
| `MEMORY_RETRIEVED` | permitted memories loaded and cited |
| `CANDIDATE_PLAN_CREATED` | a concrete activity + time exists |
| `CONTACT_IN_PROGRESS` | a call is live |
| `FRIEND_ACCEPTED` | contact said yes to specific terms |
| `FRIEND_DECLINED` | contact said no |
| `FRIEND_COUNTERPROPOSED` | contact proposed different terms |
| `PATIENT_APPROVAL_PENDING` | waiting on the patient |
| `PATIENT_ACCEPTED` / `PATIENT_DECLINED` | patient's decision |
| `AVAILABILITY_REVALIDATED` | both still free at the agreed time |
| `PLAN_CONFIRMED` | authorized by `evaluate_confirmation()` only |
| `NOTIFICATIONS_SENT` | confirmations delivered (or simulated) |
| `COMPLETE` | terminal |
| `HALTED_CONSENT_REVOKED` | terminal; reachable from every live state |
| `FAILED` | terminal; carries a visible reason |

## Transition graph

```
OPPORTUNITY_IDENTIFIED ──▶ CONSENT_VERIFIED ──▶ MEMORY_RETRIEVED
                                                      │
                                                      ▼
                        ┌──────────────── CANDIDATE_PLAN_CREATED
                        │                             │
                        │                             ▼
                        │                   CONTACT_IN_PROGRESS
                        │                   │        │        │
                        │      ┌────────────┘        │        └────────────┐
                        │      ▼                     ▼                     ▼
                        │ FRIEND_ACCEPTED    FRIEND_COUNTERPROPOSED  FRIEND_DECLINED
                        │      │                     │                     │
                        └──────┼─────────────────────┘                     ▼
                               ▼                                       COMPLETE
                     PATIENT_APPROVAL_PENDING
                          │            │
                          ▼            ▼
                 PATIENT_ACCEPTED   PATIENT_DECLINED ──▶ COMPLETE
                          │
                          ▼
                AVAILABILITY_REVALIDATED
                          │
                          ▼
                    PLAN_CONFIRMED ──▶ NOTIFICATIONS_SENT ──▶ COMPLETE

HALTED_CONSENT_REVOKED  ◀── from ANY live state
```

## Properties the tests enforce

**A counterproposal never confirms directly.** `FRIEND_COUNTERPROPOSED` leads only to
`CANDIDATE_PLAN_CREATED`. `plans.counterpropose()` bumps `version` and **clears both
participants' responses**, because changed terms invalidate prior consent.

**A mid-call re-plan returns to `CONTACT_IN_PROGRESS`.** The friend is still on the
line, so a following "yes" stays legal. This was a real bug found by testing the most
likely improvised path ("can we do Sunday?" → "yeah sounds good").

**A friend may change their mind after accepting.** `FRIEND_ACCEPTED` and
`PATIENT_APPROVAL_PENDING` both allow `FRIEND_COUNTERPROPOSED` and `FRIEND_DECLINED`,
because a human retracting before the other party answers is normal, not an error.

**The obvious LLM shortcuts are impossible.** `CONTACT_IN_PROGRESS → PLAN_CONFIRMED`
and `FRIEND_ACCEPTED → PLAN_CONFIRMED` both raise.

**Revocation is an interrupt.** Legal from every non-terminal state, so
`POST /api/consents/revoke` halts in-flight work immediately.

## The confirmation gate

`evaluate_confirmation(current, patient, contact, plan_activity, plan_starts_at)` is the
only authorization for `PLAN_CONFIRMED`. It requires **all** of:

1. state is not `HALTED_CONSENT_REVOKED`
2. both approvals present
3. both `response == "accepted"`
4. **both approved the identical `activity`** as the plan of record
5. **both approved the identical `starts_at`** as the plan of record
6. state is exactly `AVAILABILITY_REVALIDATED`

Each party's agreed terms are stored on `plan_participants.responded_activity` /
`responded_starts_at` at the moment they answer — not inferred later. That is what makes
"Josh said Sunday, Sam approved Saturday" a hard refusal rather than a silent confirm.

Refusals are **not** cached in the idempotency table, so a caller may legitimately retry
after obtaining the missing approval. Successes are cached, so replaying a confirmation
returns the stored result without re-confirming or re-notifying.
