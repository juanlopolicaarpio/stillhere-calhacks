"""Generate docs/API_CONTRACTS.md from the live FastAPI OpenAPI schema.

  python scripts/gen_api_contracts.py

Derived from the running app, so it cannot drift from the code. Re-run after any
route change. Lead-owned.
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.main import app  # noqa: E402

TAG_ORDER = ["onboarding", "companion", "demo", "events", "voice", "default"]

TAG_NOTES = {
    "onboarding": (
        "QR signup, phone verification, circle invitations and dual consent. "
        "Scanning a QR authorizes nothing; holding an invite link does not prove "
        "phone ownership."
    ),
    "companion": "Companion chat and user-controlled memory with provenance.",
    "demo": "Demo orchestration: reset, start outreach, SIMULATED patient approval, plans.",
    "events": (
        "Live event feed. Requires the dashboard token as the `X-Dashboard-Token` "
        "header OR a `dashboard_token` query parameter (EventSource cannot set headers)."
    ),
    "voice": (
        "Cartesia webhook tools (require `X-StillHere-Secret`) and the labeled local "
        "voice simulator."
    ),
    "default": "Health and capability reporting.",
}


def _schema_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def _fmt_schema(sch: dict, components: dict, depth: int = 0) -> str:
    if depth > 3:
        return "..."
    if "$ref" in sch:
        name = _schema_name(sch["$ref"])
        target = components.get(name, {})
        props = target.get("properties", {})
        if not props:
            return name
        required = set(target.get("required", []))
        parts = []
        for k, v in props.items():
            t = v.get("type") or ("enum" if "enum" in v else "any")
            if "anyOf" in v:
                t = "|".join(
                    (a.get("type") or "null") for a in v["anyOf"]
                )
            mark = "" if k in required else "?"
            parts.append(f"{k}{mark}: {t}")
        return "{" + ", ".join(parts) + "}"
    if sch.get("type") == "array":
        return f"[{_fmt_schema(sch.get('items', {}), components, depth + 1)}]"
    return sch.get("type", "any")


def main() -> int:
    spec = app.openapi()
    components = spec.get("components", {}).get("schemas", {})

    by_tag: dict[str, list] = {}
    for path, ops in sorted(spec["paths"].items()):
        for method, op in ops.items():
            if method.upper() not in {"GET", "POST", "PATCH", "DELETE", "PUT"}:
                continue
            tag = (op.get("tags") or ["default"])[0]
            by_tag.setdefault(tag, []).append((method.upper(), path, op))

    lines = [
        "# docs/API_CONTRACTS.md",
        "",
        "**Generated from the live FastAPI OpenAPI schema** by",
        "`scripts/gen_api_contracts.py`. Do not hand-edit — re-run the generator.",
        "",
        f"- Generated: {datetime.date.today().isoformat()}",
        f"- App version: {spec['info'].get('version')}",
        f"- Routes documented: {sum(len(v) for v in by_tag.values())}",
        "",
        "Hand-written contracts live in `specs/CONTRACTS.md` (frozen) and",
        "`specs/CONTRACTS_V2.md` (SpacetimeDB + fluid conversation addendum).",
        "",
        "## Error envelope",
        "",
        "All handled errors return:",
        "",
        "```json",
        '{"error": {"code": "...", "message": "..."}}',
        "```",
        "",
        "`code` is one of `validation_error`, `consent_denied`, `not_found`,",
        "`conflict`, `illegal_transition`, `provider_error`, `not_configured`,",
        "`unauthorized`. FastAPI request-validation failures return 422 with the",
        "standard `detail` body.",
        "",
        "## Authentication",
        "",
        "| surface | requirement |",
        "|---|---|",
        "| `/api/events/*` | `X-Dashboard-Token` header **or** `?dashboard_token=` |",
        "| `/api/voice/tools/*`, `/api/voice/webhook` | `X-StillHere-Secret` header |",
        "| everything else | open in this prototype (single-user demo) |",
        "",
    ]

    ordered = [t for t in TAG_ORDER if t in by_tag] + [
        t for t in sorted(by_tag) if t not in TAG_ORDER
    ]

    for tag in ordered:
        lines.append(f"## {tag}")
        lines.append("")
        if tag in TAG_NOTES:
            lines.append(TAG_NOTES[tag])
            lines.append("")
        lines.append("| method | path | request | responses |")
        lines.append("|---|---|---|---|")
        for method, path, op in sorted(by_tag[tag], key=lambda x: x[1]):
            req = "-"
            body = op.get("requestBody", {}).get("content", {}).get("application/json")
            if body:
                req = f"`{_fmt_schema(body.get('schema', {}), components)}`"
            params = op.get("parameters") or []
            qp = [
                f"{p['name']}{'' if p.get('required') else '?'}"
                for p in params
                if p.get("in") == "query"
            ]
            if qp:
                q = "query: " + ", ".join(qp)
                req = q if req == "-" else f"{req}<br>{q}"
            codes = ", ".join(sorted(op.get("responses", {}).keys()))
            summary = op.get("summary") or ""
            lines.append(
                f"| {method} | `{path}` | {req} | {codes} |"
            )
            if summary and summary.lower() not in path.lower():
                pass
        lines.append("")

    lines += [
        "## SpacetimeDB reducer surface",
        "",
        "Coordination state is written through reducers, not HTTP. See",
        "`specs/CONTRACTS_V2.md` §2-§3 and `docs/SPACETIMEDB_ARCHITECTURE.md`.",
        "",
        "| reducer | role |",
        "|---|---|",
        "| `start_session` | open a coordination session |",
        "| `set_call_state` | call lifecycle (no phone numbers stored) |",
        "| `propose_or_revise_plan` | insert an immutable proposal version |",
        "| `respond_as_friend` | record the contact's answer to a specific version |",
        "| `record_patient_approval` | record the patient's answer to a specific version |",
        "| `confirm_if_matching` | **the only way a plan can confirm** |",
        "| `revoke_or_cancel` | halt everything for a session |",
        "| `append_sanitized_event` | append to the public event feed |",
        "",
        "Called over HTTP as",
        "`POST {SPACETIMEDB_HOST}/v1/database/{SPACETIMEDB_DB}/call/{reducer}` with a",
        "JSON array of positional arguments and a Bearer token.",
        "",
    ]

    out = ROOT / "docs" / "API_CONTRACTS.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)}")
    print(f"  {sum(len(v) for v in by_tag.values())} routes across {len(by_tag)} tags")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
