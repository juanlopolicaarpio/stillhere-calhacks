/**
 * Still Here - SpacetimeDB module entry point.
 *
 * Implements the eight privileged reducers plus the one open `demo_presence`
 * reducer from specs/CONTRACTS_V2.md section 2, and the `confirm_if_matching`
 * invariant from section 3.
 *
 * Every reducer runs inside ONE SpacetimeDB transaction. If it throws, nothing
 * it wrote is committed. That is the whole point: `confirm_if_matching` reads
 * the proposal, both approvals and the consent snapshot and writes the
 * confirmation atomically, so there is no window in which a revision can race
 * a confirmation.
 *
 * ARGUMENT ENCODING NOTE (documented deviation, see docs/SPACETIMEDB_ARCHITECTURE.md)
 * ---------------------------------------------------------------------------------
 * Table columns use `t.option(...)` exactly as the contract specifies, but
 * reducer ARGUMENTS use flat scalars with sentinels, because the HTTP
 * `/v1/database/{db}/call/{reducer}` body is a plain JSON array of positional
 * args and a SATS sum value is awkward to express there:
 *   - optional strings: `""` means "none"
 *   - optional u32 (`supersedesVersion`): `0` means "none" (versions start at 1)
 * The reducer converts the sentinel to `undefined` before it reaches the row,
 * so what is STORED is a true option.
 */
import { SenderError, t, type InferSchema, type ReducerCtx } from 'spacetimedb/server';
import spacetimedb, { planProposals } from './schema';

export { default } from './schema';

type Ctx = ReducerCtx<InferSchema<typeof spacetimedb>>;

/** Machine-readable rejection reasons. Frozen by CONTRACTS_V2.md section 3. */
const REASON = {
  noProposal: 'no_proposal',
  staleVersion: 'stale_version',
  superseded: 'superseded',
  missingContactApproval: 'missing_contact_approval',
  missingPatientApproval: 'missing_patient_approval',
  activityMismatch: 'activity_mismatch',
  timeMismatch: 'time_mismatch',
  consentRevoked: 'consent_revoked',
  alreadyConfirmed: 'already_confirmed',
} as const;

const VALID_RESPONSES = ['accepted', 'declined', 'counterproposed'];

/** `""` is the wire sentinel for an absent optional string. */
function opt(value: string): string | undefined {
  return value === '' ? undefined : value;
}

/** `0` is the wire sentinel for an absent optional version. */
function optVersion(value: number): number | undefined {
  return value === 0 ? undefined : value;
}

/**
 * Privilege gate. `ctx.sender` is the authenticated principal - never an
 * identity passed as an argument. Only identities seeded into the non-public
 * `service_principals` table may mutate coordination state.
 */
function assertPrincipal(ctx: Ctx): void {
  if (ctx.db.servicePrincipals.identity.find(ctx.sender) === null) {
    throw new SenderError('not_a_service_principal');
  }
}

/** Latest-wins consent snapshot for a session, or null if none was ever written. */
function consentFor(ctx: Ctx, sessionId: string) {
  const rows = [...ctx.db.consentState.sessionId.filter(sessionId)];
  if (rows.length === 0) return null;
  return rows.reduce((a, b) =>
    b.updatedAt.microsSinceUnixEpoch >= a.updatedAt.microsSinceUnixEpoch ? b : a
  );
}

function touchSession(ctx: Ctx, sessionId: string, state: string): void {
  const session = ctx.db.connectionSessions.sessionId.find(sessionId);
  if (session !== null) {
    ctx.db.connectionSessions.sessionId.update({
      ...session,
      state,
      updatedAt: ctx.timestamp,
    });
  }
}

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

/**
 * Seeds `service_principals` with the publishing identity. `init` runs once,
 * at first publish, and its `ctx.sender` is whoever ran `spacetime publish`,
 * which is the same identity the backend's CLI token authenticates as in local
 * development.
 */
export const init = spacetimedb.init(ctx => {
  if (ctx.db.servicePrincipals.identity.find(ctx.sender) === null) {
    ctx.db.servicePrincipals.insert({
      identity: ctx.sender,
      label: 'publisher',
    });
  }
});

export const onConnect = spacetimedb.clientConnected(_ctx => {
  // Presence is recorded explicitly by the `demo_presence` reducer so that the
  // label is meaningful. Nothing to do on raw connect.
});

export const onDisconnect = spacetimedb.clientDisconnected(ctx => {
  if (ctx.connectionId !== null) {
    ctx.db.demoPresence.connectionId.delete(ctx.connectionId.toHexString());
  }
});

/**
 * DOCUMENTED DEVIATION - no clientVisibilityFilter on `service_principals`.
 *
 * CONTRACTS_V2.md section 2 asks for a `clientVisibilityFilter` on any
 * non-public table. SpacetimeDB 2.13.0 REFUSES that combination. The real
 * publish error was:
 *
 *   Error: failed to create row-level security:
 *   `SELECT * FROM service_principals WHERE identity = :sender`:
 *   Cannot define RLS rule on private table: service_principals.
 *   Please make table public if you wish to restrict access using RLS.
 *
 * RLS is the mechanism for narrowing a PUBLIC table. `public: false` is
 * strictly stronger: the table is not subscribable at all, by anyone, so there
 * is nothing for a filter to narrow. Making the table public just to attach a
 * filter would be a downgrade, so the table stays private and the filter is
 * omitted. The protected-projection capability is demonstrated instead by the
 * `activePlanProposals` view below, which is a real RLS-style narrowing of a
 * public table.
 */

/**
 * A protected, computed projection: subscribers receive ONLY the currently
 * active proposal versions, never the superseded ones. The row set is
 * recomputed by the database, not by the client, so a client cannot widen it.
 */
export const activePlanProposals = spacetimedb.anonymousView(
  { name: 'active_plan_proposals', public: true },
  t.array(planProposals.rowType),
  ctx => ctx.from.planProposals.where(p => p.active.eq(true))
);

// ---------------------------------------------------------------------------
// 1. start_session
// ---------------------------------------------------------------------------

/**
 * Upsert the session and seed its consent snapshot.
 *
 * Seeding `consent_state` here is deliberate: `confirm_if_matching` FAILS
 * CLOSED when no snapshot exists, so a session that was never started through
 * a reducer can never produce a confirmation.
 */
export const startSession = spacetimedb.reducer(
  { name: 'start_session' },
  {
    sessionId: t.string(),
    patientRef: t.string(),
    contactRef: t.string(),
    mode: t.string(),
  },
  (ctx, { sessionId, patientRef, contactRef, mode }) => {
    assertPrincipal(ctx);

    const existing = ctx.db.connectionSessions.sessionId.find(sessionId);
    if (existing === null) {
      ctx.db.connectionSessions.insert({
        sessionId,
        patientRef,
        contactRef,
        state: 'OPPORTUNITY_IDENTIFIED',
        mode,
        createdAt: ctx.timestamp,
        updatedAt: ctx.timestamp,
      });
    } else {
      ctx.db.connectionSessions.sessionId.update({
        ...existing,
        patientRef,
        contactRef,
        state: 'OPPORTUNITY_IDENTIFIED',
        mode,
        updatedAt: ctx.timestamp,
      });
    }

    const consentId = `consent_${sessionId}`;
    const consent = ctx.db.consentState.consentId.find(consentId);
    if (consent === null) {
      ctx.db.consentState.insert({
        consentId,
        sessionId,
        patientOutreachActive: true,
        contactOptedIn: false,
        contactPhoneVerified: false,
        allowedActions: [],
        revoked: false,
        reason: 'session started',
        updatedAt: ctx.timestamp,
      });
    }
  }
);

// ---------------------------------------------------------------------------
// 2. set_call_state
// ---------------------------------------------------------------------------

/**
 * Upsert the live call row. `answeredAt` / `endedAt` are derived from status by
 * the module, not supplied by the caller, so the timeline cannot be forged.
 *
 * `toNumberRedacted` MUST already be redacted (`***1234`). The module stores
 * whatever it is given, so the backend is the enforcement point; the test
 * `test_spacetimedb.py::test_no_private_data_in_public_tables` checks the
 * published rows for anything phone-shaped.
 */
export const setCallState = spacetimedb.reducer(
  { name: 'set_call_state' },
  {
    callId: t.string(),
    sessionId: t.string(),
    providerCallId: t.string(),
    toNumberRedacted: t.string(),
    status: t.string(),
    mode: t.string(),
    simulated: t.bool(),
    error: t.string(),
  },
  (
    ctx,
    { callId, sessionId, providerCallId, toNumberRedacted, status, mode, simulated, error }
  ) => {
    assertPrincipal(ctx);

    const answered = status === 'answered' || status === 'in_progress';
    const ended = status === 'completed' || status === 'failed' || status === 'ended';

    const existing = ctx.db.callSessions.callId.find(callId);
    if (existing === null) {
      ctx.db.callSessions.insert({
        callId,
        sessionId,
        providerCallId: opt(providerCallId),
        toNumberRedacted,
        status,
        mode,
        simulated,
        requestedAt: ctx.timestamp,
        answeredAt: answered ? ctx.timestamp : undefined,
        endedAt: ended ? ctx.timestamp : undefined,
        error: opt(error),
      });
    } else {
      ctx.db.callSessions.callId.update({
        ...existing,
        sessionId,
        providerCallId: opt(providerCallId) ?? existing.providerCallId,
        toNumberRedacted,
        status,
        mode,
        simulated,
        // A timestamp is stamped once, on the first transition into that state.
        answeredAt: existing.answeredAt ?? (answered ? ctx.timestamp : undefined),
        endedAt: existing.endedAt ?? (ended ? ctx.timestamp : undefined),
        error: opt(error) ?? existing.error,
      });
    }
  }
);

// ---------------------------------------------------------------------------
// 3. propose_or_revise_plan
// ---------------------------------------------------------------------------

/**
 * Insert an IMMUTABLE proposal version.
 *
 * A revision does three things in one transaction:
 *   1. inserts the new version row with `active = true`
 *   2. flips every other proposal in the session to `active = false`
 *   3. DELETES every `participant_responses` row bound to a now-superseded
 *      version
 *
 * Step 3 is the safety property: changing the terms invalidates prior consent,
 * so a v1 approval can never be laundered into a v2 confirmation.
 */
export const proposeOrRevisePlan = spacetimedb.reducer(
  { name: 'propose_or_revise_plan' },
  {
    proposalId: t.string(),
    sessionId: t.string(),
    version: t.u32(),
    activity: t.string(),
    location: t.string(),
    startsAt: t.string(),
    whyContact: t.string(),
    whyActivity: t.string(),
    memoryCitations: t.array(t.string()),
    supersedesVersion: t.u32(),
  },
  (
    ctx,
    {
      proposalId,
      sessionId,
      version,
      activity,
      location,
      startsAt,
      whyContact,
      whyActivity,
      memoryCitations,
      supersedesVersion,
    }
  ) => {
    assertPrincipal(ctx);

    if (ctx.db.planProposals.proposalId.find(proposalId) !== null) {
      // Proposal rows are immutable. A revision must use a NEW proposalId.
      throw new SenderError('proposal_already_exists');
    }
    if (version === 0) {
      throw new SenderError('version_must_be_positive');
    }

    const consent = consentFor(ctx, sessionId);
    if (consent !== null && consent.revoked) {
      throw new SenderError(REASON.consentRevoked);
    }

    ctx.db.planProposals.insert({
      proposalId,
      sessionId,
      version,
      activity,
      location: opt(location),
      startsAt,
      whyContact,
      whyActivity,
      memoryCitations,
      supersedesVersion: optVersion(supersedesVersion),
      active: true,
      createdAt: ctx.timestamp,
    });

    for (const prior of [...ctx.db.planProposals.sessionId.filter(sessionId)]) {
      if (prior.proposalId === proposalId) continue;
      if (prior.active) {
        ctx.db.planProposals.proposalId.update({ ...prior, active: false });
      }
      // Responses tied to a superseded version are void, not merely stale.
      for (const resp of [...ctx.db.participantResponses.proposalId.filter(prior.proposalId)]) {
        ctx.db.participantResponses.responseId.delete(resp.responseId);
      }
    }

    touchSession(ctx, sessionId, 'CANDIDATE_PLAN_CREATED');
  }
);

// ---------------------------------------------------------------------------
// 4 + 5. respond_as_friend / record_patient_approval
// ---------------------------------------------------------------------------

/**
 * Shared body for both response reducers. The `role` is decided by WHICH
 * reducer was called, never by an argument, so a caller cannot record an
 * approval on the other party's behalf by passing a different role string.
 */
function recordResponse(
  ctx: Ctx,
  role: 'patient' | 'contact',
  args: {
    responseId: string;
    proposalId: string;
    version: number;
    response: string;
    agreedActivity: string;
    agreedStartsAt: string;
    simulated: boolean;
  }
): void {
  assertPrincipal(ctx);

  if (!VALID_RESPONSES.includes(args.response)) {
    throw new SenderError('invalid_response');
  }

  const proposal = ctx.db.planProposals.proposalId.find(args.proposalId);
  if (proposal === null) throw new SenderError(REASON.noProposal);
  if (proposal.version !== args.version) throw new SenderError(REASON.staleVersion);
  if (!proposal.active) throw new SenderError(REASON.superseded);

  const consent = consentFor(ctx, proposal.sessionId);
  if (consent !== null && consent.revoked) throw new SenderError(REASON.consentRevoked);

  // A party may change their mind; their latest answer replaces the earlier one.
  for (const prior of [...ctx.db.participantResponses.proposalId.filter(args.proposalId)]) {
    if (prior.role === role) {
      ctx.db.participantResponses.responseId.delete(prior.responseId);
    }
  }

  ctx.db.participantResponses.insert({
    responseId: args.responseId,
    proposalId: args.proposalId,
    version: args.version,
    role,
    response: args.response,
    // An accept with no explicit terms means "the plan as proposed".
    agreedActivity: args.agreedActivity === '' ? proposal.activity : args.agreedActivity,
    agreedStartsAt: args.agreedStartsAt === '' ? proposal.startsAt : args.agreedStartsAt,
    simulated: args.simulated,
    respondedAt: ctx.timestamp,
  });
}

const responseParams = {
  responseId: t.string(),
  proposalId: t.string(),
  version: t.u32(),
  response: t.string(),
  agreedActivity: t.string(),
  agreedStartsAt: t.string(),
  simulated: t.bool(),
};

export const respondAsFriend = spacetimedb.reducer(
  { name: 'respond_as_friend' },
  responseParams,
  (ctx, args) => {
    recordResponse(ctx, 'contact', args);
  }
);

export const recordPatientApproval = spacetimedb.reducer(
  { name: 'record_patient_approval' },
  responseParams,
  (ctx, args) => {
    recordResponse(ctx, 'patient', args);
  }
);

// ---------------------------------------------------------------------------
// 6. confirm_if_matching - THE gate
// ---------------------------------------------------------------------------

/**
 * The ONLY writer of `plan_confirmations`, and therefore the only way a plan
 * can become confirmed anywhere in the system. All seven conditions of
 * CONTRACTS_V2.md section 3 are evaluated inside this single transaction.
 *
 * Check order is chosen so the rejection reason is always the MOST specific
 * true statement about why it refused:
 *   no_proposal -> already_confirmed -> stale_version -> consent_revoked
 *   -> superseded -> missing_contact_approval -> missing_patient_approval
 *   -> activity_mismatch -> time_mismatch
 *
 * Idempotency: replaying the SAME `idempotencyKey` is a silent success and
 * inserts nothing. A DIFFERENT key against an already-confirmed proposal is a
 * rejection (`already_confirmed`), never a second confirmation.
 */
export const confirmIfMatching = spacetimedb.reducer(
  { name: 'confirm_if_matching' },
  {
    proposalId: t.string(),
    version: t.u32(),
    idempotencyKey: t.string(),
  },
  (ctx, { proposalId, version, idempotencyKey }) => {
    assertPrincipal(ctx);

    // (1a) the proposal row exists
    const proposal = ctx.db.planProposals.proposalId.find(proposalId);
    if (proposal === null) throw new SenderError(REASON.noProposal);

    // (7) no existing confirmation for this proposal - with idempotent replay
    const confirmed = ctx.db.planConfirmations.proposalId.find(proposalId);
    if (confirmed !== null) {
      if (confirmed.idempotencyKey === idempotencyKey) {
        return; // exact replay: success, nothing written
      }
      throw new SenderError(REASON.alreadyConfirmed);
    }

    // (2) the passed version matches the proposal's version
    if (proposal.version !== version) throw new SenderError(REASON.staleVersion);

    // (6) consent snapshot says not revoked. FAIL CLOSED on a missing snapshot:
    // a session that never went through `start_session` cannot confirm.
    const consent = consentFor(ctx, proposal.sessionId);
    if (consent === null || consent.revoked) throw new SenderError(REASON.consentRevoked);

    // (1b) not superseded by a later revision
    if (!proposal.active) throw new SenderError(REASON.superseded);

    const responses = [...ctx.db.participantResponses.proposalId.filter(proposalId)];

    // (3) an accepted contact response for THIS exact proposalId AND version
    const contact = responses.find(
      r => r.role === 'contact' && r.response === 'accepted' && r.version === version
    );
    if (contact === undefined) throw new SenderError(REASON.missingContactApproval);

    // (4) likewise for the patient
    const patient = responses.find(
      r => r.role === 'patient' && r.response === 'accepted' && r.version === version
    );
    if (patient === undefined) throw new SenderError(REASON.missingPatientApproval);

    // (5) both agreed to the proposal's terms AND to each other's terms
    if (
      contact.agreedActivity !== proposal.activity ||
      patient.agreedActivity !== proposal.activity ||
      contact.agreedActivity !== patient.agreedActivity
    ) {
      throw new SenderError(REASON.activityMismatch);
    }
    if (
      contact.agreedStartsAt !== proposal.startsAt ||
      patient.agreedStartsAt !== proposal.startsAt ||
      contact.agreedStartsAt !== patient.agreedStartsAt
    ) {
      throw new SenderError(REASON.timeMismatch);
    }

    ctx.db.planConfirmations.insert({
      confirmationId: `conf_${proposalId}_v${version}`,
      proposalId,
      version,
      activity: proposal.activity,
      startsAt: proposal.startsAt,
      idempotencyKey,
      confirmedAt: ctx.timestamp,
    });

    touchSession(ctx, proposal.sessionId, 'PLAN_CONFIRMED');
  }
);

// ---------------------------------------------------------------------------
// 7. revoke_or_cancel
// ---------------------------------------------------------------------------

/**
 * Consent revocation is an interrupt. In one transaction it marks the consent
 * snapshot revoked, deactivates every proposal for the session, voids every
 * outstanding response, and halts the session. After this runs,
 * `confirm_if_matching` has two independent reasons to refuse.
 */
export const revokeOrCancel = spacetimedb.reducer(
  { name: 'revoke_or_cancel' },
  { sessionId: t.string(), reason: t.string() },
  (ctx, { sessionId, reason }) => {
    assertPrincipal(ctx);

    const consentId = `consent_${sessionId}`;
    const consent = ctx.db.consentState.consentId.find(consentId);
    if (consent === null) {
      ctx.db.consentState.insert({
        consentId,
        sessionId,
        patientOutreachActive: false,
        contactOptedIn: false,
        contactPhoneVerified: false,
        allowedActions: [],
        revoked: true,
        reason,
        updatedAt: ctx.timestamp,
      });
    } else {
      ctx.db.consentState.consentId.update({
        ...consent,
        patientOutreachActive: false,
        revoked: true,
        reason,
        updatedAt: ctx.timestamp,
      });
    }
    // Any other snapshot rows for this session are revoked too.
    for (const row of [...ctx.db.consentState.sessionId.filter(sessionId)]) {
      if (row.consentId !== consentId && !row.revoked) {
        ctx.db.consentState.consentId.update({
          ...row,
          revoked: true,
          reason,
          updatedAt: ctx.timestamp,
        });
      }
    }

    for (const proposal of [...ctx.db.planProposals.sessionId.filter(sessionId)]) {
      if (proposal.active) {
        ctx.db.planProposals.proposalId.update({ ...proposal, active: false });
      }
      for (const resp of [...ctx.db.participantResponses.proposalId.filter(proposal.proposalId)]) {
        ctx.db.participantResponses.responseId.delete(resp.responseId);
      }
    }

    touchSession(ctx, sessionId, 'HALTED_CONSENT_REVOKED');
  }
);

// ---------------------------------------------------------------------------
// 7b. set_consent_state - ADDITION beyond CONTRACTS_V2.md section 2
// ---------------------------------------------------------------------------

/**
 * ADDED REDUCER (reported to the lead as a deviation).
 *
 * Section 1 requires the sanitized consent snapshot to live in SpacetimeDB,
 * but section 2 lists no reducer that can mirror a consent CHANGE - only
 * `revoke_or_cancel`, which can merely revoke. Without this, the snapshot could
 * never move past the defaults seeded by `start_session`, so
 * `contactOptedIn` / `contactPhoneVerified` would be permanently false and the
 * table would be decorative.
 *
 * It can never UN-revoke: once revoked, revoked stays true. Only booleans and a
 * reason string cross the boundary - never the verification code, never the
 * phone number.
 */
export const setConsentState = spacetimedb.reducer(
  { name: 'set_consent_state' },
  {
    sessionId: t.string(),
    patientOutreachActive: t.bool(),
    contactOptedIn: t.bool(),
    contactPhoneVerified: t.bool(),
    allowedActions: t.array(t.string()),
    reason: t.string(),
  },
  (
    ctx,
    { sessionId, patientOutreachActive, contactOptedIn, contactPhoneVerified, allowedActions, reason }
  ) => {
    assertPrincipal(ctx);

    const consentId = `consent_${sessionId}`;
    const existing = ctx.db.consentState.consentId.find(consentId);
    if (existing === null) {
      ctx.db.consentState.insert({
        consentId,
        sessionId,
        patientOutreachActive,
        contactOptedIn,
        contactPhoneVerified,
        allowedActions,
        revoked: false,
        reason,
        updatedAt: ctx.timestamp,
      });
    } else {
      ctx.db.consentState.consentId.update({
        ...existing,
        patientOutreachActive,
        contactOptedIn,
        contactPhoneVerified,
        allowedActions,
        // Revocation is one-way. This reducer cannot resurrect consent.
        revoked: existing.revoked,
        reason,
        updatedAt: ctx.timestamp,
      });
    }
  }
);

// ---------------------------------------------------------------------------
// 8. append_sanitized_event
// ---------------------------------------------------------------------------

/**
 * Append-only sanitized dashboard feed. Re-appending the same `eventId` is a
 * no-op rather than an error so that a backend retry cannot break a workflow.
 */
export const appendSanitizedEvent = spacetimedb.reducer(
  { name: 'append_sanitized_event' },
  {
    eventId: t.string(),
    seq: t.u64(),
    sessionId: t.string(),
    proposalId: t.string(),
    callId: t.string(),
    eventType: t.string(),
    safeSummary: t.string(),
    payloadJson: t.string(),
    simulated: t.bool(),
  },
  (
    ctx,
    {
      eventId,
      seq,
      sessionId,
      proposalId,
      callId,
      eventType,
      safeSummary,
      payloadJson,
      simulated,
    }
  ) => {
    assertPrincipal(ctx);

    if (ctx.db.agentEvents.eventId.find(eventId) !== null) return;

    ctx.db.agentEvents.insert({
      eventId,
      seq,
      sessionId: opt(sessionId),
      proposalId: opt(proposalId),
      callId: opt(callId),
      eventType,
      safeSummary,
      payloadJson,
      simulated,
      at: ctx.timestamp,
    });
  }
);

// ---------------------------------------------------------------------------
// 9. upsert_demo_presence - the ONE reducer any client may call
// ---------------------------------------------------------------------------

/**
 * Heartbeat used to prove that two independently subscribed clients see each
 * other with no polling and no page reload. Deliberately NOT privileged: an
 * anonymous browser client can call it. It writes nothing but a label and a
 * server timestamp, so there is no privilege to escalate.
 *
 * NAME DEVIATION (reported to the lead): CONTRACTS_V2.md section 2 calls this
 * "the `demo_presence` upsert". It CANNOT be named `demo_presence`, because
 * SpacetimeDB derives a PascalCase arg type from the reducer name and that
 * collides with the `demo_presence` TABLE's row type. The real publish error
 * was: `name \`DemoPresence\` is used for multiple types`. Hence
 * `upsert_demo_presence`.
 */
export const upsertDemoPresence = spacetimedb.reducer(
  { name: 'upsert_demo_presence' },
  { connectionId: t.string(), label: t.string() },
  (ctx, { connectionId, label }) => {
    const key = connectionId === '' && ctx.connectionId !== null
      ? ctx.connectionId.toHexString()
      : connectionId;
    if (key === '') throw new SenderError('missing_connection_id');

    const existing = ctx.db.demoPresence.connectionId.find(key);
    if (existing === null) {
      ctx.db.demoPresence.insert({ connectionId: key, label, lastSeen: ctx.timestamp });
    } else {
      ctx.db.demoPresence.connectionId.update({ ...existing, label, lastSeen: ctx.timestamp });
    }
  }
);

/**
 * Clear every coordination table in ONE transaction.
 *
 * Exists for two callers that both need a clean slate atomically:
 *   - `POST /api/demo/reset`, so a rehearsal starts from nothing;
 *   - the test suite's `fresh_db` fixture.
 *
 * Why a reducer rather than a series of DELETEs: the module is a persistent
 * external process that outlives a test, so leftover rows made the suite
 * non-hermetic (tests passed alone, failed together). Clearing over SQL took
 * eight HTTP round-trips and ~2.4s per test, which is ten minutes of overhead
 * across the suite. One reducer call is a single round-trip and a single
 * transaction, so no caller can ever observe a half-cleared database.
 *
 * `service_principals` is deliberately NOT cleared - wiping it would lock
 * every privileged reducer out of its own database.
 */
export const resetCoordination = spacetimedb.reducer(
  { name: 'reset_coordination' },
  (ctx) => {
    assertPrincipal(ctx);
    for (const row of [...ctx.db.planConfirmations.iter()]) {
      ctx.db.planConfirmations.confirmationId.delete(row.confirmationId);
    }
    for (const row of [...ctx.db.participantResponses.iter()]) {
      ctx.db.participantResponses.responseId.delete(row.responseId);
    }
    for (const row of [...ctx.db.planProposals.iter()]) {
      ctx.db.planProposals.proposalId.delete(row.proposalId);
    }
    for (const row of [...ctx.db.consentState.iter()]) {
      ctx.db.consentState.consentId.delete(row.consentId);
    }
    for (const row of [...ctx.db.callSessions.iter()]) {
      ctx.db.callSessions.callId.delete(row.callId);
    }
    for (const row of [...ctx.db.connectionSessions.iter()]) {
      ctx.db.connectionSessions.sessionId.delete(row.sessionId);
    }
    for (const row of [...ctx.db.agentEvents.iter()]) {
      ctx.db.agentEvents.eventId.delete(row.eventId);
    }
    for (const row of [...ctx.db.demoPresence.iter()]) {
      ctx.db.demoPresence.connectionId.delete(row.connectionId);
    }
  }
);
