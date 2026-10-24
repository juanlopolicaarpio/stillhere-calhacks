/**
 * Still Here - SpacetimeDB module schema.
 *
 * Canonical schema from specs/CONTRACTS_V2.md section 2. SpacetimeDB is the
 * authoritative transactional store for the SHARED live coordination workflow.
 * SQLite (backend/app/db.py) keeps everything private to one person.
 *
 * SENSITIVE-BY-CONSTRUCTION RULE
 * -----------------------------
 * Every table marked `public: true` here is subscribable by any connected
 * client, so it may only ever hold sanitized, shareable projections. No raw
 * phone number, verification code, invitation token, conversation transcript,
 * or private memory value is permitted in any column below. The only phone
 * related column is `call_sessions.to_number_redacted`, which the backend
 * redacts to `***1234` BEFORE it is ever passed to a reducer.
 *
 * `service_principals` is the single non-public table: it holds the identities
 * allowed to call privileged reducers. It carries NO clientVisibilityFilter,
 * because SpacetimeDB 2.13.0 rejects RLS on a private table outright ("Cannot
 * define RLS rule on private table"). `public: false` is the stronger
 * guarantee anyway - the table is not subscribable at all. See the long note
 * in `index.ts` and docs/SPACETIMEDB_ARCHITECTURE.md.
 */
import { table, schema, t } from 'spacetimedb/server';

/** One row per demo / outreach session. Refs are opaque SQLite ids, not names. */
const connectionSessions = table(
  { name: 'connection_sessions', public: true },
  {
    sessionId: t.string().primaryKey(),
    patientRef: t.string(),
    contactRef: t.string(),
    state: t.string(),
    mode: t.string(),
    createdAt: t.timestamp(),
    updatedAt: t.timestamp(),
  }
);

/**
 * Live call state. `toNumberRedacted` is the ONLY phone-shaped column in the
 * whole module and it never holds more than the last four digits.
 */
const callSessions = table(
  { name: 'call_sessions', public: true },
  {
    callId: t.string().primaryKey(),
    sessionId: t.string().index('btree'),
    providerCallId: t.option(t.string()),
    toNumberRedacted: t.string(),
    status: t.string(),
    mode: t.string(),
    simulated: t.bool(),
    requestedAt: t.timestamp(),
    answeredAt: t.option(t.timestamp()),
    endedAt: t.option(t.timestamp()),
    error: t.option(t.string()),
  }
);

/**
 * IMMUTABLE plan versions. A revision never mutates an existing row: it
 * INSERTS a new row with a higher `version` and flips every prior row for the
 * session to `active = false`. That is what makes a stale approval
 * unconfirmable - the approval is bound to a (proposalId, version) pair.
 */
const planProposals = table(
  { name: 'plan_proposals', public: true },
  {
    proposalId: t.string().primaryKey(),
    sessionId: t.string().index('btree'),
    version: t.u32(),
    activity: t.string(),
    location: t.option(t.string()),
    startsAt: t.string(),
    whyContact: t.string(),
    whyActivity: t.string(),
    memoryCitations: t.array(t.string()),
    supersedesVersion: t.option(t.u32()),
    active: t.bool(),
    createdAt: t.timestamp(),
  }
);

/**
 * Who agreed to WHAT, keyed to one exact proposal version. Storing the agreed
 * terms (not just "yes") is what lets `confirm_if_matching` reject
 * "Josh said Sunday, Sam approved Saturday".
 */
const participantResponses = table(
  { name: 'participant_responses', public: true },
  {
    responseId: t.string().primaryKey(),
    proposalId: t.string().index('btree'),
    version: t.u32(),
    role: t.string(), // 'patient' | 'contact'
    response: t.string(), // 'accepted' | 'declined' | 'counterproposed'
    agreedActivity: t.string(),
    agreedStartsAt: t.string(),
    simulated: t.bool(),
    respondedAt: t.timestamp(),
  }
);

/** Sanitized consent snapshot: booleans and a reason string, never field values. */
const consentState = table(
  { name: 'consent_state', public: true },
  {
    consentId: t.string().primaryKey(),
    sessionId: t.string().index('btree'),
    patientOutreachActive: t.bool(),
    contactOptedIn: t.bool(),
    contactPhoneVerified: t.bool(),
    allowedActions: t.array(t.string()),
    revoked: t.bool(),
    reason: t.string(),
    updatedAt: t.timestamp(),
  }
);

/**
 * Sanitized dashboard feed. `payloadJson` arrives ALREADY redacted by
 * backend/app/services/events.py::redact - the module cannot verify that, so
 * the backend is the enforcement point and this table is append-only.
 */
const agentEvents = table(
  { name: 'agent_events', public: true },
  {
    eventId: t.string().primaryKey(),
    seq: t.u64(),
    sessionId: t.option(t.string()),
    proposalId: t.option(t.string()),
    callId: t.option(t.string()),
    eventType: t.string(),
    safeSummary: t.string(),
    payloadJson: t.string(),
    simulated: t.bool(),
    at: t.timestamp(),
  }
);

/** Written ONLY by `confirm_if_matching`. There is no other writer. */
const planConfirmations = table(
  { name: 'plan_confirmations', public: true },
  {
    confirmationId: t.string().primaryKey(),
    proposalId: t.string().unique(),
    version: t.u32(),
    activity: t.string(),
    startsAt: t.string(),
    idempotencyKey: t.string().unique(),
    confirmedAt: t.timestamp(),
  }
);

/** Proves two independently subscribed clients sync with no polling. */
const demoPresence = table(
  { name: 'demo_presence', public: true },
  {
    connectionId: t.string().primaryKey(),
    label: t.string(),
    lastSeen: t.timestamp(),
  }
);

/**
 * NOT public. Identities permitted to call privileged reducers. Seeded in
 * `init` with the publishing identity (`ctx.sender` of the init reducer).
 */
const servicePrincipals = table(
  { name: 'service_principals', public: false },
  {
    identity: t.identity().primaryKey(),
    label: t.string(),
  }
);

const spacetimedb = schema({
  connectionSessions,
  callSessions,
  planProposals,
  participantResponses,
  consentState,
  agentEvents,
  planConfirmations,
  demoPresence,
  servicePrincipals,
});

export default spacetimedb;

/**
 * Exported ONLY so `index.ts` can reference `planProposals.rowType` as the
 * declared return type of the `active_plan_proposals` view. This is a
 * compile-time schema builder, not a module export: `index.ts` does not
 * re-export it, so SpacetimeDB never sees it as a registered export.
 */
export { planProposals };
