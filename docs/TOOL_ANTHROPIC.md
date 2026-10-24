# Anthropic

## 1. Status

`implemented (code) / untested against live API`. Both the Claude call path
and a deterministic rule-based fallback exist and are exercised by tests, but
no `ANTHROPIC_API_KEY` was supplied to this build, so the live Claude path has
never actually been called.

## 2. Role in Still Here

Powers companion chat replies and structured memory extraction (turning a
user's message into durable fact/preference/relationship/boundary records).
When no key is configured, both functions degrade to a deterministic,
honestly-labeled fallback rather than failing or pretending to be Claude.

## 3. Credentials required

| Env var | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Auth for `anthropic.Anthropic` client |
| `ANTHROPIC_MODEL` | Model id, defaults to `claude-opus-5` |

Neither is set in this build (`config.anthropic_configured` is `False`).

## 4. SDK / package / version as actually verified

`anthropic` **2.3.0**, verified via `pip show anthropic` in the project
virtualenv.

## 5. Setup steps

1. Create a key at the Anthropic Console.
2. Set `ANTHROPIC_API_KEY` (and optionally `ANTHROPIC_MODEL`) in `.env`.
3. No further setup — `backend/app/adapters/anthropic_adapter.py` picks it up
   automatically through `config.get_settings()`; no restart-order dependency
   beyond the normal app startup.

## 6. Actual implemented API calls

- `backend/app/adapters/anthropic_adapter.py:89` `companion_reply()` →
  `client.messages.create(model=s.anthropic_model, max_tokens=400,
  system=COMPANION_SYSTEM + mem_block, messages=msgs)`. On any exception,
  falls back to `_fallback_reply()` and sets `mode="fallback"` with the error
  recorded (truncated to 300 chars).
- `backend/app/adapters/anthropic_adapter.py:184` `extract_memories()` →
  `client.messages.create(model=s.anthropic_model, max_tokens=800,
  system=EXTRACTION_SYSTEM, messages=[{"role": "user", "content": message}])`,
  parses the response as a strict JSON array of typed memory candidates. Any
  parse failure or exception falls back to `_fallback_extract()`.
- Both functions are gated by `available()` at
  `backend/app/adapters/anthropic_adapter.py:35`, which returns
  `get_settings().anthropic_configured` — `False` in this build, so every
  call in this environment currently takes the fallback branch, not the
  `client.messages.create` branch.

## 7. How to test

- `python -m pytest backend/tests -q` → 113 passed (exercises both the
  fallback logic and the code paths that would call Claude, via whatever key
  state the test environment has — in this build, the fallback).
- `python scripts/e2e_demo.py` → 49 checks passed, 0 failed.
- To exercise the real API: set `ANTHROPIC_API_KEY`, restart the backend, and
  confirm `GET /api/status` reports `anthropic_configured: true`; a companion
  chat response's `reasoning_mode` field should then read `"claude"` instead
  of `"fallback"`.

## 8. Known limitations

- No request has ever been made against the live Anthropic API in this
  build; everything described as "Claude-powered" currently runs the
  deterministic fallback.
- The fallback is real logic (regex-based name/keyword extraction, template
  replies), not a stub — but it is not Claude, and the API/UI label it
  honestly as `mode: "fallback"` / `reasoning_mode: "fallback"`.
- No retry/backoff logic beyond a single try/except around the API call;
  any transient failure immediately degrades to fallback for that request.
