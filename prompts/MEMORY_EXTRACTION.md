# prompts/MEMORY_EXTRACTION.md

The memory-extraction prompt, as implemented in
`backend/app/adapters/anthropic_adapter.py` (`EXTRACTION_SYSTEM`). The code is
authoritative.

Model: `claude-opus-5`, `max_tokens=800`. Input is ONE user message. Output is parsed as
JSON; a fenced code block is tolerated and stripped. Any parse failure falls back to
`_fallback_extract()` and reports `extraction_mode: "fallback"`.

---

## System prompt (verbatim)

```
Extract durable, user-stated facts from ONE message.

Return ONLY a JSON array. Each element:
  {"kind": "fact"|"preference"|"relationship"|"boundary",
   "value": "<short third-person statement>",
   "subject_name": "<person it is about, or null>",
   "confidence": 0.0-1.0,
   "source_quote": "<the exact substring of the message it came from>"}

Rules:
- Only what the user actually said. Never infer a diagnosis, a mental state, a
  risk level, or anything clinical. Never invent.
- "boundary" is for limits and dislikes: "doesn't want crowded theaters".
- "relationship" is for who someone is: "Josh is Sam's best friend".
- If the message contains nothing durable, return [].
- Output the raw JSON array only. No prose, no markdown fence.
```

## Post-processing (server-side, not model-controlled)

Every candidate passes through `services/companion.handle_message()`:

| step | code | behaviour |
|---|---|---|
| kind validation | `anthropic_adapter.extract_memories` | unknown kind coerced to `fact` |
| length cap | same | `value` and `source_quote` truncated to 300 chars |
| sensitivity classification | `memory.classify_sensitivity` | clinical language ⇒ `private_to_sam`, else `agent_internal` |
| subject resolution | `companion._resolve_subject` | a name is mapped to a real `user_id` only when an accepted relationship exists |
| provenance | `memory.store_memory` | `source_conversation_id` + `source_quote` + `confidence` always recorded |

**The model never chooses visibility.** Classification is a deterministic function so a
prompt injection cannot widen the audience for a clinical fact. A memory can only become
shareable when the user changes it, via `PATCH /api/memories/{id}`.

## Deterministic fallback

`_fallback_extract()` splits on sentence boundaries and applies real rules:
relationship words plus a capitalised name produce a `relationship`; negation patterns
(`don't`, `hate`, `avoid`, `rather not`, `not into`) produce a `boundary`; affinity
patterns (`used to`, `we would`, `love`, `enjoy`, `favorite`, `miss`) produce a
`preference`. Capped at 5 per message.

This is a genuine rule-based extractor, not a stub, and it is what powers the demo when
no Anthropic key is present. It is why "I don't want crowded theaters anymore" still
flips the planner from the movie to coffee with no LLM in the loop.
