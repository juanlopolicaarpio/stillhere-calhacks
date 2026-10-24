# Longitudinal baseline & opportunity detection

Status: **implemented** (specs/CONTRACTS_V3.md §1 vocabulary).
Owner: agent G. Spec: `specs/CONTRACTS_V3.md` §2.

Files:

| file | role |
|---|---|
| `backend/app/services/baseline.py` | measures each relationship's contact cadence |
| `backend/app/services/opportunity.py` | turns measurements into scored, suppressible opportunities |
| `backend/tests/test_baseline.py` | 71 tests, all graded behaviours asserted |
| `backend/app/db.py:24-37, 62-65, 260, 295-302` | the `contact_events` table + its additive migration |

---

## 1. Why this exists

The demo used to assert "Sam was discharged three weeks ago, so it has been a
while since Josh". That is a *story*, and a story cannot be wrong in an
interesting way. The product directive (`docs/extraclaude.txt`) asks for
"genuine longitudinal baseline calculations and opportunity detection rather
than a hardcoded three-week scenario", and for the backend to "independently
detect meaningful changes in connection patterns".

So the scenario stays as **seed data**, and the **detection is arithmetic**.
Nothing downstream hardcodes "Josh" or "47 days": change the seeded cadence and
the detector's output, its ranking and its sentences all change with it
(`backend/tests/test_baseline.py::test_reason_and_basis_are_not_canned_phrases`).

Two properties are non-negotiable, and both are structural rather than
aspirational:

1. **No model is involved.** `relationship_baseline`, `all_baselines`,
   `detect`, `cooling_off` and `score_baseline` are pure functions over SQLite.
   Same rows in, same objects out, every time
   (`test_detection_is_deterministic`, `test_detection_makes_no_llm_call`,
   `test_detection_writes_nothing`). Claude proposes the *activity* afterwards,
   in `backend/app/agents/orchestrator.py`; it is never asked whether an
   opportunity exists. A judge asking "why did it decide to reach out?" gets an
   arithmetic answer.
2. **Clinical data never enters.** `baseline.py` reads `users`,
   `relationships` and `contact_events` and nothing else. `opportunity.py`
   touches memory only through `memory.retrieve(..., audience="planning")`,
   whose allow-list (`backend/app/services/memory.py:198-209`) excludes
   `private_to_sam` by construction rather than by threshold.

---

## 2. The data

`contact_events`, added additively in `backend/app/db.py`:

```sql
CREATE TABLE IF NOT EXISTS contact_events (
  id TEXT PRIMARY KEY,
  patient_id TEXT NOT NULL,
  contact_id TEXT NOT NULL,
  kind TEXT NOT NULL,          -- call | message | met_in_person
                               -- | plan_completed | plan_declined
  occurred_at TEXT NOT NULL,   -- ISO 8601, UTC
  source TEXT NOT NULL DEFAULT 'observed',   -- seed | observed | user_reported
  note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_contact_events_pair
  ON contact_events(patient_id, contact_id, occurred_at);
```

Deliberately **no foreign keys**, matching the §2 contract, so the history
survives `wipe_all()` reordering and so a `user_reported` backfill for someone
not yet in `users` is possible.

The table is appended to `SCHEMA` (`db.py:260`) *and* created by the additive
table-migration loop in `init_db()` (`db.py:295-302`), which checks
`sqlite_master` and only runs DDL for a table that is absent — so a database
created before this feature upgrades in place on the next boot.
`test_contact_events_table_exists_after_init` asserts the migration ran.

Writes go through `baseline.record_contact_event`
(`backend/app/services/baseline.py:161`), which validates `kind` and `source`
against the frozen vocabularies and normalises `occurred_at` to tz-aware UTC.
It is **append-only** on purpose: a `plan_declined` row is a safety signal and
must not be quietly rewritten.

---

## 3. The baseline algorithm

`baseline.relationship_baseline(patient_id, contact_id, *, now=None)`
(`baseline.py:379`).

### 3.1 Gaps

```
times  = sorted(parse(e.occurred_at) for e in contact_events(pair))
gaps   = [(times[i+1] - times[i]).total_seconds() / 86400
          for i in range(len(times) - 1)]          # days, clamped at >= 0
```

The sort is load-bearing, not decorative: events can be inserted out of order
(a `user_reported` backfill of last month's coffee), and lexical ordering of
mixed-offset ISO strings is not chronological ordering
(`test_events_inserted_out_of_order_are_sorted_before_measuring`).

### 3.2 Median and mean

```
median_gap_days = statistics.median(gaps)    # None when len(gaps) == 0
mean_gap_days   = statistics.fmean(gaps)     # None when len(gaps) == 0
```

**Why the median, not the mean.** Real friendships have outliers — one holiday,
one work trip, one evening of five messages. The mean absorbs them; the median
does not. Concretely, with gaps `200, 7, 7, 7, 7`:

| statistic | value | verdict on a 3-day-old contact |
|---|---|---|
| mean | 45.6 days | 0.07x — "we basically just spoke", wildly wrong |
| **median** | **7.0 days** | 0.43x — correct: this is a weekly friendship |

Asserted in `test_median_is_robust_to_an_outlier_that_wrecks_the_mean`. The
mean is still reported, because the *distance between the two* is itself
evidence: `_basis_sentence` says the gaps are skewed whenever they differ by a
day or more, and `_confidence` downgrades erratic cadences.

### 3.3 Current gap

```
last_contact = max(t for t in times if t <= now)             # past events only
                or relationships.last_contact_at             # secondary source
current_gap_days = max(0.0, (now - last_contact) / 1 day)    # never negative
```

`relationships.last_contact_at` (maintained by the rest of the app) overrides
the event log only when it is fresher by more than
`REL_LAST_CONTACT_PREFERENCE_HOURS` (1 hour, `baseline.py:83`), and the basis
sentence says when that happened. The one-hour margin stops a seed that writes
the relationship row and the matching event microseconds apart from reading as
a disagreement between two sources.

Future-dated events never produce a negative gap: they are excluded from
"last contact", `has_future_dated_events` is set, and the basis explains it
(`test_future_dated_event_never_produces_a_negative_gap`). If *every* event is
in the future, the gap is `0.0`, not `None`.

### 3.4 Deviation ratio

```
deviation_ratio = current_gap_days / max(median_gap_days, MIN_GAP_FLOOR_DAYS)
```

`MIN_GAP_FLOOR_DAYS = 1.0` (`baseline.py:73`) exists for exactly one case:
every recorded contact falling on the same calendar day gives `median == 0`,
and dividing by it is meaningless rather than merely illegal. One day is the
smallest honest unit of "a gap" here, and the basis sentence states that the
floor was applied rather than hiding it
(`test_all_events_on_the_same_day_uses_a_one_day_floor`).

`deviation_ratio` is **`None`**, never a guess, when there is no computable
median (0 or 1 events). A caller needing a number must decide for itself what
absence means; `opportunity.score_baseline` has an explicit, penalised branch.

### 3.5 Trend

| condition | trend |
|---|---|
| `deviation_ratio >= 2.5` (`DORMANT_RATIO`) | `dormant` |
| `deviation_ratio >= 1.5` (`DRIFTING_RATIO`) | `drifting` |
| otherwise | `stable` |

With no computable median, the fallback is absolute elapsed time — `>= 35` days
dormant, `>= 21` days drifting (`NO_BASELINE_DORMANT_DAYS`,
`NO_BASELINE_DRIFTING_DAYS`, `baseline.py:67-68`) — and the basis says the
reading "rests on elapsed time alone". Thresholds are inclusive at the
boundary (`test_trend_thresholds_are_inclusive_at_the_boundary`).

### 3.6 Confidence model

```
events < 4  (MIN_EVENTS_FOR_BASELINE)   -> "low"
events >= 6 (STRONG_EVIDENCE_EVENTS)    -> "high"
otherwise                               -> "medium"
then: stdev(gaps) > 1.0 * median        -> drop one level
```

Fewer than four events is **not** a cadence, it is anecdote, and the contract
requires detection to *say so* rather than invent precision. It does:
`confidence == "low"` and the basis reads e.g. *"With only 3 events (fewer than
the 4 needed) this baseline is weak: confidence is low and these figures should
be read as a hint, not a measured habit."*
(`test_fewer_than_four_events_is_low_confidence_and_says_so`, parametrised over
0, 1, 2 and 3 events.)

The erratic-gaps demotion (`ERRATIC_STDEV_RATIO`, `baseline.py:77`) catches the
case the event count alone misses: eight events with gaps
`1, 1, 90, 1, 1, 120, 1` is plenty of data describing no habit at all, so it
caps at `medium` (`test_erratic_gaps_cost_a_level_of_confidence`).

Confidence is not cosmetic — it multiplies the score (§5), so a weak baseline
provably cannot produce a strong one
(`test_low_confidence_caps_the_opportunity_score`).

### 3.7 `basis`

Assembled clause by clause from measured values in `_basis_sentence`
(`baseline.py:268`). Every clause carries a real number; none is selected from
a list of canned phrases. It names the event count, the date span, the
individual gaps, the median, the mean, whether they are skewed, the current
gap, the ratio, the trend, the confidence, and any degenerate condition that
applied (same-day floor, future-dated event, fallback to
`last_contact_at`). This is what the judge-facing dashboard renders.

---

## 4. `all_baselines`

`baseline.all_baselines(patient_id, *, now=None, statuses=None)`
(`baseline.py:489`). Covers every contact with a relationship row, plus — when
`statuses is None` — any contact with recorded history but no relationship row,
so nothing silently disappears. Sorted most-deviant first
(`-deviation_ratio`, then `contact_id`), with unmeasurable relationships last.
Deterministic for a given database state.

It does **not** filter by consent. That is the caller's job, and
`opportunity.detect` does it (§6).

---

## 5. Scoring

`opportunity.score_baseline(baseline, *, has_shared_time)`
(`opportunity.py:309`):

```
deviation = min(deviation_ratio, 6.0) / 6.0                       # 0 .. 1
   # no median available:
deviation = min(current_gap_days / 35.0, 1.0) * 0.5               # weak prior

raw       = deviation * CONFIDENCE_WEIGHT[confidence] + TREND_BONUS[trend]
            #           low 0.5 / medium 0.8 / high 1.0
            #                             stable 0.0 / drifting 0.10 / dormant 0.20

score     = min(max(raw, 0), 1.0) * (1.0 if has_shared_time else 0.4)
```

* **Saturation at 6.0x** (`DEVIATION_SATURATION`): a gap 20x the usual is not
  meaningfully more urgent than 6x, and letting it score higher would let one
  very old relationship permanently crowd out everybody else
  (`test_score_saturates_so_one_contact_cannot_run_away_with_it`).
* **No shared free time costs 0.4x** rather than disqualifying: the opportunity
  is real, the patient could ask for a new window, but it is not actionable
  today. `Opportunity.actionable` reports this separately.
* Monotonic in the deviation ratio by construction
  (`test_score_is_monotonic_in_deviation_ratio`).

`MIN_SCORE_TO_RAISE = 0.25` (`opportunity.py:108`) is the floor `best()` uses.
It is reached at roughly the `drifting` threshold on a high-confidence baseline
with shared time, so `best()` returns `None` while everyone is on their normal
cadence (`test_best_stays_silent_when_everyone_is_on_their_normal_cadence`).
"Nothing to raise right now" is a correct answer, and frequently the kindest.

---

## 6. Suppression — harm reduction, not politeness

This is the most important behaviour in the module, and the reason to read the
rest of it sceptically if this part were ever weakened.

The target population is adults in the first 30 days after discharge from
inpatient psychiatric care. For that person, a system that reads "no reply" as
"try harder" does not merely annoy — it manufactures the exact feeling of being
a burden that makes someone stop answering their phone at all. An escalating
nag aimed at someone recovering from psychiatric hospitalisation is actively
harmful. So suppression here is a clinical safety property, not good manners.

### 6.1 What suppresses

`opportunity.suppressing_signals` (`opportunity.py:180`) reads three
independent sources, because a decline can arrive three ways:

| source | condition |
|---|---|
| `contact_events` | a row of `kind = 'plan_declined'` |
| `plan_participants` | `response = 'declined'` on one of this patient's plans with this contact |
| `calls` | `answered_at IS NULL` and `status IN ('no_answer','busy','voicemail','failed')` |

`failed` is included deliberately: from the contact's side a failed call is
still an attempt, and the safe reading of a technical failure is to back off
rather than retry in a loop. Future-dated signals are ignored — a signal cannot
suppress before it happens
(`test_future_dated_decline_does_not_suppress_before_it_happens`).

### 6.2 The window

`opportunity.cooling_off` (`opportunity.py:258`), default
`COOLING_OFF_DAYS = 14`:

```
anchor    = most recent suppressing signal at or before now
until     = anchor + max(window_days, COOLING_OFF_DAYS) days
suppressed = until > now
```

Three structural guarantees:

1. **It cannot be shortened.** `max(window_days, COOLING_OFF_DAYS)` means a
   caller passing `window_days=1` gets 14 anyway
   (`test_cooling_off_window_cannot_be_configured_shorter_than_the_floor`).
   It *is* configurable upward
   (`test_cooling_off_window_is_configurable_upward`).
2. **More silence only extends it.** The window is anchored to the *most
   recent* signal, so a second decline moves the anchor — and the release date
   — forward (`test_a_second_decline_extends_the_window_and_never_shortens_it`,
   `test_more_silence_cannot_shorten_the_window_or_raise_the_score`).
3. **The score cannot rise.** In `detect`, the score is computed from the
   baseline and then *zeroed* if suppressed (`opportunity.py:485-490`). There
   is no arithmetic path by which a decline contributes positively. Separately,
   a `plan_declined` row counts as a *contact* in the cadence maths — the
   friend was reached and answered — which pulls `deviation_ratio` **down**,
   never up (`test_a_decline_can_only_lower_the_deviation_ratio_never_raise_it`).

Every day inside the window is checked by
`test_every_day_inside_the_window_is_suppressed`, and release at the exact
boundary by `test_suppression_lasts_the_full_window_and_then_releases`.

### 6.3 What the user-visible text says

`suppressed_reason` is plain words with the real dates:

> Outreach is paused: declined a proposed plan on 2026-10-22 (3 days ago).
> Still Here waits 14 days after a decline or an unanswered call before raising
> this contact again, so nothing will be proposed until 2026-11-05 — 11 more
> days. A decline or a silence never shortens that wait and never increases the
> score; it only ever pushes the next check further out.

### 6.4 Suppressed, not hidden

A suppressed opportunity is still **returned** by `detect()`, with
`score = 0.0`, so the dashboard can show that the system noticed and chose to
wait (`test_suppressed_opportunity_is_still_reported_not_hidden`). Suppression
you can see is a feature; suppression you cannot see is indistinguishable from
a bug. `best()` (`opportunity.py:524`) will never hand a suppressed contact to
the planner (`test_best_refuses_to_return_a_suppressed_contact`).

### 6.5 Consent

`detect()` only considers relationships whose status is in
`ELIGIBLE_RELATIONSHIP_STATUSES = {"accepted"}` (`opportunity.py:75`). Daniel
in the demo seed was invited and never accepted, so he never appears — even
with 200 days of fabricated dormancy
(`test_never_accepted_contact_is_not_an_opportunity`). His absence is a consent
guarantee, not an oversight.

---

## 7. Privacy

`reason`, `basis`, `suppressed_reason` and `supporting_memories` are built from
display names, dates, slot times and numbers. The only memory access anywhere
in detection is `_planning_memories` (`opportunity.py:335`), with two
independent guards mirroring `memory.shareable_context`:

1. `memory.retrieve(..., audience="planning")` — the allow-list excludes
   `private_to_sam`, so the seed's hospitalisation and diagnosis rows are
   structurally unreachable.
2. `memory.assert_no_sensitive_leak` on every surviving value — so a
   *mislabelled* `agent_internal` row that still reads clinically is dropped
   anyway.

Tests: `test_no_clinical_term_leaks_into_any_detection_output` runs memory.py's
own outbound guard over every output string of every opportunity and every
baseline; `test_detection_never_reads_private_memory_rows` plants a
`private_to_sam` row naming Josh and the inpatient ward and asserts neither
word surfaces; `test_a_mislabelled_clinical_row_is_still_blocked_by_the_content_guard`
plants an `agent_internal` row mentioning a diagnosis and asserts guard 2
catches it.

---

## 8. Worked example — the seeded demo, real numbers

`baseline.seed_demo_history_defaults` (`baseline.py:608`) writes the fictional
cadences in `baseline.py:546-552`. With `now = 2026-10-25T12:00:00Z`:

### Josh — the dormant one

Seeded gaps `8, 12, 9, 14, 11, 10, 13` days → 8 events; last contact 47 days
ago.

```
sorted gaps     = 8, 9, 10, 11, 12, 13, 14
median          = 11.0 days
mean            = 77 / 7 = 11.0 days
current gap     = 47.0 days
deviation ratio = 47.0 / 11.0 = 4.2727... -> 4.27
trend           = 4.27 >= 2.5             -> dormant
confidence      = 8 events, stdev 2.16 < 11.0 -> high

deviation = min(4.27, 6) / 6 = 0.7117
raw       = 0.7117 * 1.0 (high) + 0.20 (dormant) = 0.9117
slots     = 2 shared free windows -> factor 1.0
score     = 0.9117
```

Actual `basis`:

> 8 contacts with Josh are on record between 2026-06-22 and 2026-09-07, with 7
> gaps of 8, 12, 9, 14, 11, 10, 13 days. The median gap is 11.0 days and the
> mean is 11.0 days, so the cadence is fairly even. It has now been 47.0 days
> since the last contact, 4.27x the usual gap, which reads as dormant.
> Confidence is high on 8 events.

Actual `reason`:

> Josh (best friend): normally in touch about every 11.0 days across 8 recorded
> contacts; it has been 47.0 days, which is 4.27x the usual gap. That reads as
> dormant at high confidence. 2 shared free windows in the next 14 days, the
> earliest at Sun 25 Oct 11:00 UTC. 4 planning-visible memories reference Josh.

### Maya — recently in touch

Seeded gaps `6, 9, 7, 11, 8, 10` → 7 events; last contact 9 days ago.

```
sorted gaps     = 6, 7, 8, 9, 10, 11
median          = (8 + 9) / 2 = 8.5 days
mean            = 51 / 6     = 8.5 days
current gap     = 9.0 days
deviation ratio = 9.0 / 8.5  = 1.0588... -> 1.06
trend           = 1.06 < 1.5             -> stable
confidence      = high

deviation = 1.06 / 6 = 0.1767
raw       = 0.1767 * 1.0 + 0.0 = 0.1767
slots     = 0 (the seed gives Maya no overlap with Sam) -> factor 0.4
score     = 0.0707
```

### Daniel — never opted in

No contact history, no `last_contact_at`. `median`, `mean`, `current_gap` and
`deviation_ratio` are all `None`; `confidence` is `low`; the basis reads *"No
contact history is recorded for Daniel yet, and the relationship row carries no
last-contact date either, so no cadence can be measured. Treating this as
unknown rather than quiet."* He is excluded from `detect()` entirely because
his relationship status is `invited`.

### Ranking

`detect("user_sam")` → `[Josh 0.9117, Maya 0.0707]`.
`best("user_sam")` → Josh (0.9117 ≥ 0.25). After a `plan_declined` for Josh,
`detect` → `[Maya 0.0707, Josh 0.0 suppressed]` and `best` → `None`: Maya is on
her normal cadence and Josh is inside his cooling-off window, so the correct
action is to do nothing.

Asserted end to end in
`test_seeded_demo_numbers_are_exactly_what_the_docs_claim`.

---

## 9. Wiring (for the lead)

### 9.1 `demo_reset.py`

Add one call at the end of `seed()`, after the availability block and before
the `demo_sessions` insert:

```python
from . import baseline as baseline_svc        # top of file, with the other imports

    # Longitudinal contact history, so "it has been a while" is computed from
    # real gaps rather than asserted. Josh comes out dormant (47 days against
    # an 11-day median); Maya comes out stable (9 days against 8.5).
    baseline_svc.seed_demo_history_defaults(
        SAM, dormant_contact_id=JOSH, recent_contact_id=MAYA
    )
```

It is idempotent (`clear=True` removes each pair's existing rows first), so
repeated demo rehearsals are safe
(`test_seed_demo_history_is_idempotent`).

### 9.2 `agents/orchestrator.py`

The detector replaces the "who" half of `identify_opportunity`:

```python
from ..services import opportunity as opportunity_svc

opp = opportunity_svc.best(patient_id)        # -> Opportunity | None
if opp is None:
    # Either nobody has drifted, or every candidate is in a cooling-off
    # window. Both are correct reasons to do nothing.
    return {"ok": False, "reason": "no opportunity to raise right now"}

contact_id = opp.contact_id        # who
starts_at  = opp.shared_free_slots[0]["start_at"]   # when (or keep next_slot())
why_contact = opp.reason           # already cites the real numbers
```

Full surface:

```python
baseline.record_contact_event(patient_id, contact_id, kind, *,
                              occurred_at=None, source="observed", note="") -> str
baseline.relationship_baseline(patient_id, contact_id, *, now=None) -> Baseline
baseline.all_baselines(patient_id, *, now=None, statuses=None) -> list[Baseline]
baseline.seed_demo_history_defaults(patient_id, *, dormant_contact_id,
                                    recent_contact_id=None, now=None) -> dict
opportunity.detect(patient_id, *, now=None) -> list[Opportunity]
opportunity.best(patient_id, *, now=None, min_score=0.25) -> Opportunity | None
opportunity.cooling_off(patient_id, contact_id, *, now=None,
                        window_days=14) -> (reason|None, until|None, signal|None)
```

`Baseline.as_dict()` and `Opportunity.as_dict()` are JSON-ready (the latter
nests the baseline and adds the `suppressed` / `actionable` booleans), so an
API route or the dashboard can serialise them directly.

### 9.3 Keeping history fresh in production

`record_contact_event` should be called wherever a real interaction concludes:

| site | suggested call |
|---|---|
| `voice.end_call` on an answered call | `kind="call"` |
| `plans.record_response(..., "accepted")` | `kind="plan_completed"` after the plan's start time |
| `plans.record_response(..., "declined")` | `kind="plan_declined"` — also already picked up from `plan_participants` |

Detection deliberately emits **no** events of its own, so it stays pure and
`detect()` can be called on every dashboard refresh without growing a row
(`test_detection_writes_nothing`). Any `agent_events` row describing a detected
opportunity should be emitted by the caller.

---

## 10. Status and known gaps

**Status: `implemented`.** 71 tests in `backend/tests/test_baseline.py` pass,
and the full backend suite passes with no regression. Not
`externally_verified`: this feature has no third-party dependency to verify
against — it is arithmetic over the project's own database, so the tests *are*
the evidence.

Known gaps, honestly stated:

* `db.wipe_all()` does not clear `contact_events` (that function is lead-owned
  and outside agent G's permitted edit). `conftest.fresh_db` deletes the whole
  database file, so tests are unaffected; a long-lived demo database will
  accumulate rows across `demo_reset.reset()` calls.
  `seed_demo_history(clear=True)` deletes the seeded pairs' rows first, so the
  demo numbers stay correct regardless — but `contact_events` should be added
  to the `wipe_all` table list when the lead next touches `db.py`.
* Nothing yet *writes* `contact_events` in production paths; §9.3 lists the
  call sites. Until they are wired, live history comes only from the seed and
  from `relationships.last_contact_at`.
* The unanswered-call suppression query matches on `contact_id` alone, because
  the `calls` table has no `patient_id` (a call need not have a plan behind
  it). In a multi-patient database this **over**-suppresses: an unanswered
  call to Josh made on another patient's behalf would also pause Sam's
  outreach to Josh. Over-suppression is the safe direction to be wrong in, so
  it is left as-is rather than loosened; it needs a `patient_id` column on
  `calls` (lead-owned) to fix properly.
* `trend` has no `"unknown"` member, because the §2 contract fixes it to three
  values. With no measurable history the honest reading is "unknown", and it is
  reported as `stable` with `confidence="low"`, `deviation_ratio=None` and a
  basis that says plainly that nothing can be measured. Callers must read
  `confidence`, not `trend` alone.
* Scoring weights (`CONFIDENCE_WEIGHT`, `TREND_BONUS`,
  `DEVIATION_SATURATION`, `NO_SHARED_TIME_FACTOR`) are reasoned defaults, not
  values fitted to data. They are all collected at the top of
  `opportunity.py:60-108` so they can be tuned in one place.
