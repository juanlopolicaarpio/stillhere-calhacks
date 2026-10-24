"""SpacetimeDB adapter - the ONLY path from Python into the coordination module.

Verified against `spacetime` CLI / `spacetimedb-lib` 2.13.0 and a local server at
http://127.0.0.1:3000 on 2026-10-24. Contract: specs/CONTRACTS_V2.md section 4.

MEASURED HTTP SHAPES (not assumed - every one of these was observed)
--------------------------------------------------------------------
Reducer call:
    POST {host}/v1/database/{db}/call/{reducer}
    Authorization: Bearer <jwt>
    Content-Type: application/json
    body: JSON ARRAY of positional args, e.g. ["sess_1", "pat", "con", "simulated"]
    -> 200 with an EMPTY body on success (reducers never return data; read
       state back through SQL or a subscription)
    -> 530 with the thrown message as the PLAIN-TEXT body on rejection, e.g.
       the literal bytes `time_mismatch`. This is how the machine-readable
       rejection reasons from CONTRACTS_V2.md section 3 reach Python.

SQL query:
    POST {host}/v1/database/{db}/sql
    Content-Type: text/plain
    body: raw SQL
    -> 200 with [{"schema": {"elements": [{"name": {"some": "col"},
                                           "algebraic_type": {...}}, ...]},
                  "rows": [[v, ...], ...]}]

       Rows are POSITIONAL and the VALUES ARE POSITIONAL TOO - this is
       BSATN-JSON, not a friendly keyed object, and it cannot be read without
       the per-column `algebraic_type`. Measured, with the raw bytes:
         option some : [0, "prov_123"]      -> "prov_123"
         option none : [1, []]              -> None
         timestamp   : [1792855029577003]   -> 1792855029577003 (micros)
         identity    : ["0xc2005b09..."]    -> "0xc2005b09..."
         array       : ["mem_1"]            -> ["mem_1"]
       `_decode()` walks the algebraic type to produce plain Python values.

SECURITY
--------
Nothing in this module logs the bearer token, and nothing logs a reducer's
argument values. Reducer arguments can carry a redacted number
(`call_sessions.to_number_redacted`) and, more importantly, a buggy caller
could pass something it should not - so argument values are never written to a
log line. Only the reducer NAME and the ARGUMENT COUNT are ever logged.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("stillhere.spacetimedb")

DEFAULT_HOST = "http://127.0.0.1:3000"
DEFAULT_DB = "stillhere"

#: `spacetime` CLI config that holds the local development JWT.
CLI_TOML = (
    Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    / "SpacetimeDB"
    / "config"
    / "cli.toml"
)

#: Set once per process so the dev-token warning is not emitted on every call.
_warned_cli_token = False


class SpacetimeNotConfigured(Exception):
    """Raised when host / database / token are not all available."""


class SpacetimeError(Exception):
    """Transport or HTTP failure talking to SpacetimeDB.

    `reason` carries the module's machine-readable rejection string when the
    failure was a reducer rejection (one of `no_proposal`, `stale_version`,
    `superseded`, `missing_contact_approval`, `missing_patient_approval`,
    `activity_mismatch`, `time_mismatch`, `consent_revoked`,
    `already_confirmed`). It is None for transport-level failures.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.reason = reason


def host() -> str:
    return (os.environ.get("SPACETIMEDB_HOST") or DEFAULT_HOST).strip().rstrip("/")


def database() -> str:
    return (os.environ.get("SPACETIMEDB_DB") or DEFAULT_DB).strip()


def _cli_token() -> str:
    """Read `spacetimedb_token` out of the CLI's cli.toml.

    LOCAL DEVELOPMENT ONLY. The CLI token is the publishing identity, which is
    exactly the identity seeded into `service_principals`, so it is what makes
    privileged reducers callable from a dev box without extra setup. It must
    never be the credential in a deployed environment - set SPACETIMEDB_TOKEN
    there instead.
    """
    global _warned_cli_token
    try:
        text = CLI_TOML.read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.search(r'^\s*spacetimedb_token\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if m is None:
        return ""
    if not _warned_cli_token:
        _warned_cli_token = True
        log.warning(
            "SPACETIMEDB_TOKEN is unset; using local CLI token (dev only) from %s. "
            "Set SPACETIMEDB_TOKEN explicitly for any non-local deployment.",
            CLI_TOML,
        )
    return m.group(1).strip()


def token() -> str:
    """The bearer token. Never logged, never returned to an API response."""
    env = (os.environ.get("SPACETIMEDB_TOKEN") or "").strip().strip('"').strip("'")
    return env or _cli_token()


def configured() -> bool:
    """True when host + database + token are all present."""
    return bool(host() and database() and token())


def _auth_headers(content_type: str) -> dict[str, str]:
    tok = token()
    if not tok:
        raise SpacetimeNotConfigured(
            "SPACETIMEDB_TOKEN is not set and no local CLI token was found at "
            f"{CLI_TOML}"
        )
    return {
        "Authorization": f"Bearer {tok}",
        "Content-Type": content_type,
    }


def call_reducer(name: str, args: list, *, timeout: float = 10.0) -> dict:
    """Invoke a reducer. One reducer call is one SpacetimeDB transaction.

    `args` are positional, in the order declared by the reducer, serialized as
    a JSON array - the shape the HTTP endpoint actually accepts.

    Returns `{"ok": True, "reducer": name, "status": 200}` on success.
    Raises SpacetimeError on rejection, with `.reason` set to the module's
    machine-readable reason string when the module supplied one.
    """
    if not configured():
        raise SpacetimeNotConfigured(
            "SpacetimeDB is not configured (need SPACETIMEDB_HOST, "
            "SPACETIMEDB_DB and a token)"
        )

    url = f"{host()}/v1/database/{database()}/call/{name}"
    # NOTE: args are deliberately absent from every log line in this function.
    log.debug("stdb call_reducer name=%s argc=%d", name, len(args))
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.post(url, json=args, headers=_auth_headers("application/json"))
    except httpx.HTTPError as e:
        raise SpacetimeError(f"transport failure calling {name}: {e}") from e

    if r.status_code == 200:
        return {"ok": True, "reducer": name, "status": 200}

    body = (r.text or "").strip()
    reason = body if _REASON_RE.fullmatch(body) else None
    log.info("stdb reducer rejected name=%s status=%s reason=%s", name, r.status_code, reason)
    raise SpacetimeError(
        f"reducer {name} failed with HTTP {r.status_code}: {body[:300]}",
        status=r.status_code,
        reason=reason,
    )


#: A rejection body is a bare machine-readable token, never prose or a stack.
_REASON_RE = re.compile(r"[a-z][a-z0-9_]{1,63}")


def _name_of(raw: Any) -> str | None:
    """Element/variant names arrive as `{"some": "x"}` or `{"none": []}`."""
    if isinstance(raw, dict):
        if "some" in raw:
            v = raw["some"]
            return v if isinstance(v, str) else None
        return None
    return raw if isinstance(raw, str) else None


def _decode(value: Any, atype: Any) -> Any:
    """Decode one BSATN-JSON value using its algebraic type.

    Values are POSITIONAL, not keyed, so the type is required to read them:
      * Sum    -> `[tagIndex, payload]`; the option variants are named
                  `some` / `none`, so `[0, v]` -> v and `[1, []]` -> None.
      * Product-> `[el0, el1, ...]` in declaration order. A single-element
                  product whose element name is dunder-wrapped is one of
                  SpacetimeDB's special scalars (`__timestamp_micros_since_
                  unix_epoch__`, `__identity__`, `__time_duration_micros__`),
                  and collapses to that inner value.
      * Array  -> a JSON array of the element type.
    """
    if not isinstance(atype, dict):
        return value

    if "Sum" in atype:
        variants = (atype["Sum"] or {}).get("variants") or []
        if not (isinstance(value, list) and len(value) == 2):
            return value
        tag, payload = value
        if not isinstance(tag, int) or tag >= len(variants):
            return value
        variant = variants[tag] or {}
        vname = _name_of(variant.get("name"))
        decoded = _decode(payload, variant.get("algebraic_type"))
        if vname == "some":
            return decoded
        if vname == "none":
            return None
        return {"tag": vname, "value": decoded}

    if "Product" in atype:
        elements = (atype["Product"] or {}).get("elements") or []
        if not isinstance(value, list):
            return value
        if len(elements) == 1:
            ename = _name_of(elements[0].get("name")) or ""
            if ename.startswith("__") and ename.endswith("__"):
                inner = value[0] if value else None
                return _decode(inner, elements[0].get("algebraic_type"))
        out: dict[str, Any] = {}
        for i, el in enumerate(elements):
            key = _name_of((el or {}).get("name")) or f"field_{i}"
            out[key] = _decode(value[i] if i < len(value) else None,
                               (el or {}).get("algebraic_type"))
        return out

    if "Array" in atype:
        inner = atype["Array"]
        if isinstance(value, list):
            return [_decode(v, inner) for v in value]
        return value

    # String / Bool / all numeric tags are already plain JSON scalars.
    return value


def _columns(schema: Any) -> list[tuple[str, Any]]:
    """`[(column_name, algebraic_type), ...]` in positional order."""
    elements = (schema or {}).get("elements") or []
    cols: list[tuple[str, Any]] = []
    for i, el in enumerate(elements):
        name = _name_of((el or {}).get("name")) or f"col_{i}"
        cols.append((name, (el or {}).get("algebraic_type")))
    return cols


def query_sql(sql: str, *, timeout: float = 10.0) -> list[dict]:
    """Run a read-only SQL query and return a list of row dicts."""
    if not configured():
        raise SpacetimeNotConfigured(
            "SpacetimeDB is not configured (need SPACETIMEDB_HOST, "
            "SPACETIMEDB_DB and a token)"
        )

    url = f"{host()}/v1/database/{database()}/sql"
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.post(url, content=sql, headers=_auth_headers("text/plain"))
    except httpx.HTTPError as e:
        raise SpacetimeError(f"transport failure running SQL: {e}") from e

    if r.status_code != 200:
        raise SpacetimeError(
            f"SQL failed with HTTP {r.status_code}: {(r.text or '')[:300]}",
            status=r.status_code,
        )

    try:
        payload = r.json()
    except ValueError as e:
        raise SpacetimeError(f"SQL returned non-JSON: {(r.text or '')[:200]}") from e

    # The endpoint returns one result object per statement.
    results = payload if isinstance(payload, list) else [payload]
    out: list[dict] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        cols = _columns(result.get("schema"))
        for row in result.get("rows") or []:
            if isinstance(row, dict):
                # Defensive: a keyed row shape would need no positional mapping.
                out.append(row)
                continue
            rec: dict[str, Any] = {}
            for i, value in enumerate(row):
                if i < len(cols):
                    name, atype = cols[i]
                    rec[name] = _decode(value, atype)
                else:
                    rec[f"col_{i}"] = value
            out.append(rec)
    return out


def health(*, timeout: float = 3.0) -> dict:
    """Cheap reachability probe. Never raises; always returns a dict.

    This is what `config.Settings.coordination_mode()` keys off, so it must be
    honest: `reachable` is True only when the named database answered a query.
    """
    db = database()
    if not configured():
        return {
            "reachable": False,
            "db": db,
            "detail": "not configured (SPACETIMEDB_TOKEN missing and no local CLI token)",
        }

    try:
        with httpx.Client(timeout=timeout) as c:
            ping = c.get(f"{host()}/v1/ping")
    except httpx.HTTPError as e:
        return {"reachable": False, "db": db, "detail": f"server unreachable: {e}"}

    if ping.status_code != 200:
        return {
            "reachable": False,
            "db": db,
            "detail": f"/v1/ping returned HTTP {ping.status_code}",
        }

    # The server is up; confirm the MODULE is actually published to this db.
    try:
        query_sql("SELECT * FROM connection_sessions LIMIT 1", timeout=timeout)
    except (SpacetimeError, SpacetimeNotConfigured) as e:
        return {
            "reachable": False,
            "db": db,
            "detail": f"server up but module '{db}' not queryable: {e}",
        }

    return {"reachable": True, "db": db, "detail": f"module '{db}' reachable at {host()}"}
