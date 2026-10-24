# docs/CALENDAR_INTEGRATION.md

Google Calendar and Microsoft Graph free/busy, plus event creation on confirmation.

> Written by the lead after agent H was terminated mid-task by an API error. Its
> code landed complete and is described here; this file is the deliverable that
> was missing, not a rewrite of the integration.

## Status

| provider | status | why |
|---|---|---|
| Google Calendar | **`blocked`** | Adapter and tests complete. Needs an OAuth client ID/secret that only a human can create in Google Cloud Console. |
| Microsoft Graph | **`blocked`** | Same: adapter and tests complete, needs an Azure app registration. |
| Seeded availability fallback | **`implemented`** | The `availability` table remains the source of truth while both providers are unconfigured. |

`blocked` is the honest status, not `implemented`. No OAuth token has ever been
exchanged, so no live free/busy query has been made. Per
`specs/CONTRACTS_V3.md` §1, that cannot be called `externally_verified` until a
real round-trip against a real Google or Microsoft account exists in
`evidence/`.

## What this changes about scheduling

Nothing, until credentials exist. `services/scheduling.find_free_slots()` keeps
reading the `availability` table, and `/api/status` reports
`calendar_mode: "seeded"`. Once a provider is connected, `POST
/api/calendar/sync` writes real busy blocks into the same table, so group
scheduling, counterproposals and revalidation all work unchanged — the
integration is a data source, not a new code path.

## Routes

Wired into `main.py` by the lead (`app.include_router(calendar_api.router)`).

| method | path | purpose |
|---|---|---|
| GET | `/api/calendar/status` | which provider is connected, per user |
| GET | `/api/calendar/{provider}/auth` | begin OAuth; returns the consent URL |
| GET | `/api/calendar/{provider}/callback` | exchange the code for tokens |
| POST | `/api/calendar/disconnect` | delete stored tokens |
| POST | `/api/calendar/sync` | pull free/busy into `availability` |

`{provider}` is `google` or `microsoft`.

## Verified endpoints

Taken from the adapter source, which agent H verified against the live docs.

**Google** (`adapters/google_calendar.py`)
- authorize: `https://accounts.google.com/o/oauth2/v2/auth`
- token / refresh: `https://oauth2.googleapis.com/token`
- revoke: `https://oauth2.googleapis.com/revoke`
- free/busy: `https://www.googleapis.com/calendar/v3/freeBusy`

**Microsoft** (`adapters/microsoft_graph.py`)
- authorize / token: `https://login.microsoftonline.com/{tenant}/oauth2/v2.0/...`
- free/busy: `https://graph.microsoft.com/v1.0/me/calendar/getSchedule`
- events: `https://graph.microsoft.com/v1.0/me/events`

## Scopes, and why they are minimal

| scope | needed for |
|---|---|
| `https://www.googleapis.com/auth/calendar.freebusy` | reading busy blocks **only** — no event titles, no attendees, no descriptions |
| `https://www.googleapis.com/auth/calendar.events.owned` | creating the confirmed plan's event, limited to events this app created |

The read scope is deliberately `freebusy` rather than `calendar.readonly`.
Still Here needs to know *when* someone is busy, never *what* they are doing —
and for this population, a calendar's contents (therapy appointments, clinic
visits) are exactly the data that must not be collected. Event creation is a
separate, later consent step, not bundled into the read request.

Microsoft equivalents are `Calendars.Read` for `getSchedule` and
`Calendars.ReadWrite` for event creation; the same split applies.

## Environment variables

```
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_OAUTH_REDIRECT_URI=http://localhost:8000/api/calendar/google/callback
GOOGLE_CALENDAR_ID=primary

MICROSOFT_CLIENT_ID=
MICROSOFT_CLIENT_SECRET=
MICROSOFT_TENANT_ID=common
MICROSOFT_OAUTH_REDIRECT_URI=http://localhost:8000/api/calendar/microsoft/callback
MICROSOFT_CALENDAR_ADDRESS=
```

## The human steps (this is what is actually blocking)

**Google**
1. console.cloud.google.com → new project
2. APIs & Services → Library → enable **Google Calendar API**
3. OAuth consent screen → External → add yourself as a test user
4. Credentials → Create → OAuth client ID → **Web application**
5. Authorized redirect URI: exactly `GOOGLE_OAUTH_REDIRECT_URI` above, character for character
6. Copy the client ID and secret into `.env`

**Microsoft**
1. portal.azure.com → Microsoft Entra ID → App registrations → New
2. Redirect URI: Web, exactly `MICROSOFT_OAUTH_REDIRECT_URI`
3. API permissions → Microsoft Graph → delegated → `Calendars.Read`
4. Certificates & secrets → new client secret
5. Copy client ID, secret and tenant ID into `.env`

Then: `GET /api/calendar/google/auth?user_id=user_sam`, open the returned URL,
consent, and the callback stores the tokens.

**A mismatched redirect URI is the overwhelmingly common failure.** It must
match what is registered byte for byte, including scheme, port and trailing path.

## Tokens

Stored in the `calendar_tokens` table (`id, user_id, provider, access_token,
refresh_token, expires_at, scopes, created_at`), added by agent H via the
additive-migration block in `db.py`.

- Never logged, never returned from a route, never written into an event payload.
- An expired access token is refreshed transparently; a dead refresh token
  surfaces as a clear error telling the user to reconnect.
- **Known gap, stated rather than hidden:** tokens are stored in plaintext in
  SQLite. Adding encryption at rest would have meant a new dependency, so it was
  not done. That is acceptable for a local prototype with fictional data and is
  **not** acceptable for real users — see `docs/SECURITY.md`.

## Timezones

Provider responses are normalised to timezone-aware UTC before they reach the
`availability` table, because `scheduling.find_free_slots()` compares instants
and silently misbehaves on naive datetimes. All-day events and multi-day spans
are expanded into explicit busy blocks rather than being skipped. The tests
cover a non-UTC timezone specifically, since that is where calendar code
usually breaks.

## Testing

```bash
python -m pytest backend/tests/test_calendar.py -q
```

1,249 lines of tests with `httpx` monkeypatched — **no network calls**. They
cover: `configured()` false with no env; `auth_url` scope/redirect/state
contents; token exchange parsing; the refresh path; a dead refresh token;
free/busy parsing including all-day, multi-day and non-UTC; busy→free inversion;
`create_event` request shape; 401/403/429/5xx handling; and that no token value
appears in any returned object, log line or exception message.

## Limitations

- Neither provider has been exercised against a live account.
- Free/busy only. Still Here never reads what an event is.
- Event creation needs a second, wider consent step and is untested live.
- No webhook/push subscription: availability refreshes when `/api/calendar/sync`
  is called, not continuously.
- Plaintext token storage, above.
