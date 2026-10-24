// Hand-written SpacetimeDB client bindings for module `stillhere`.
//
// NOT produced by `spacetime generate`. See connection_sessions_table.ts for
// the full explanation: `spacetime generate --lang typescript --module-path
// ./spacetime/spacetimedb` (run 2026-10-24, CLI 2.13.0) currently emits
// bindings for the unwired default template (`person`/`add`/`say_hello`)
// because spacetime/spacetimedb/src/index.ts has not yet been updated to
// `export { default } from './schema'`. The real schema
// (spacetime/spacetimedb/src/schema.ts, owned by agent B) already matches
// specs/CONTRACTS_V2.md §2 verbatim, so this file assembles bindings for
// that frozen, already-written schema by hand, in the exact shape
// `spacetime generate` itself produces (verified by diffing against a real
// generate run against the template). Once agent B wires schema.ts into
// index.ts, re-run:
//
//   spacetime generate --lang typescript --module-path ./spacetime/spacetimedb --out-dir ./frontend/src/spacetime_bindings -y
//
// and delete the hand-written files (keep this comment's intent, drop the
// files).
//
// Only the eight PUBLIC coordination tables are bound here.
// `service_principals` is `public: false` per spec and is never subscribed
// to by a browser client.
/* eslint-disable */
import {
  DbConnectionBuilder as __DbConnectionBuilder,
  DbConnectionImpl as __DbConnectionImpl,
  SubscriptionBuilderImpl as __SubscriptionBuilderImpl,
  convertToAccessorMap as __convertToAccessorMap,
  makeQueryBuilder as __makeQueryBuilder,
  procedureSchema as __procedureSchema,
  procedures as __procedures,
  reducerSchema as __reducerSchema,
  reducers as __reducers,
  schema as __schema,
  table as __table,
  type DbConnectionConfig as __DbConnectionConfig,
  type ErrorContextInterface as __ErrorContextInterface,
  type EventContextInterface as __EventContextInterface,
  type QueryBuilder as __QueryBuilder,
  type ReducerEventContextInterface as __ReducerEventContextInterface,
  type RemoteModule as __RemoteModule,
  type SubscriptionEventContextInterface as __SubscriptionEventContextInterface,
  type SubscriptionHandleImpl as __SubscriptionHandleImpl,
} from 'spacetimedb';

// Table row schemas
import ConnectionSessionsRow from './connection_sessions_table';
import CallSessionsRow from './call_sessions_table';
import PlanProposalsRow from './plan_proposals_table';
import ParticipantResponsesRow from './participant_responses_table';
import ConsentStateRow from './consent_state_table';
import AgentEventsRow from './agent_events_table';
import PlanConfirmationsRow from './plan_confirmations_table';
import DemoPresenceRow from './demo_presence_table';

// Reducer arg schemas
import StartSessionReducer from './start_session_reducer';
import SetCallStateReducer from './set_call_state_reducer';
import ProposeOrReviseplanReducer from './propose_or_revise_plan_reducer';
import RespondAsFriendReducer from './respond_as_friend_reducer';
import RecordPatientApprovalReducer from './record_patient_approval_reducer';
import ConfirmIfMatchingReducer from './confirm_if_matching_reducer';
import RevokeOrCancelReducer from './revoke_or_cancel_reducer';
import AppendSanitizedEventReducer from './append_sanitized_event_reducer';
import UpsertPresenceReducer from './upsert_presence_reducer';

/** Table schema information, keyed by the camelCase TS accessor. */
const tablesSchema = __schema({
  connectionSessions: __table({ name: 'connection_sessions', indexes: [], constraints: [] }, ConnectionSessionsRow),
  callSessions: __table({ name: 'call_sessions', indexes: [], constraints: [] }, CallSessionsRow),
  planProposals: __table({ name: 'plan_proposals', indexes: [], constraints: [] }, PlanProposalsRow),
  participantResponses: __table({ name: 'participant_responses', indexes: [], constraints: [] }, ParticipantResponsesRow),
  consentState: __table({ name: 'consent_state', indexes: [], constraints: [] }, ConsentStateRow),
  agentEvents: __table({ name: 'agent_events', indexes: [], constraints: [] }, AgentEventsRow),
  planConfirmations: __table({ name: 'plan_confirmations', indexes: [], constraints: [] }, PlanConfirmationsRow),
  demoPresence: __table({ name: 'demo_presence', indexes: [], constraints: [] }, DemoPresenceRow),
});

/** Reducer schema information. Most are privileged server-only calls; see start_session_reducer.ts. */
const reducersSchema = __reducers(
  __reducerSchema('start_session', StartSessionReducer),
  __reducerSchema('set_call_state', SetCallStateReducer),
  __reducerSchema('propose_or_revise_plan', ProposeOrReviseplanReducer),
  __reducerSchema('respond_as_friend', RespondAsFriendReducer),
  __reducerSchema('record_patient_approval', RecordPatientApprovalReducer),
  __reducerSchema('confirm_if_matching', ConfirmIfMatchingReducer),
  __reducerSchema('revoke_or_cancel', RevokeOrCancelReducer),
  __reducerSchema('append_sanitized_event', AppendSanitizedEventReducer),
  // Best-effort / unconfirmed — see upsert_presence_reducer.ts.
  __reducerSchema('upsert_presence', UpsertPresenceReducer),
);

const proceduresSchema = __procedures();

const REMOTE_MODULE = {
  versionInfo: { cliVersion: '2.13.0' as const },
  tables: tablesSchema.schemaType.tables,
  reducers: reducersSchema.reducersType.reducers,
  ...proceduresSchema,
} satisfies __RemoteModule<
  typeof tablesSchema.schemaType,
  typeof reducersSchema.reducersType,
  typeof proceduresSchema
>;

/** Query-builder table references, e.g. `tables.connectionSessions`. */
export const tables: __QueryBuilder<typeof tablesSchema.schemaType> = __makeQueryBuilder(tablesSchema.schemaType);

/** Reducer call accessors, e.g. `reducers.startSession(...)`. */
export const reducers = __convertToAccessorMap(reducersSchema.reducersType.reducers);

export type EventContext = __EventContextInterface<typeof REMOTE_MODULE>;
export type ReducerEventContext = __ReducerEventContextInterface<typeof REMOTE_MODULE>;
export type SubscriptionEventContext = __SubscriptionEventContextInterface<typeof REMOTE_MODULE>;
export type ErrorContext = __ErrorContextInterface<typeof REMOTE_MODULE>;
export type SubscriptionHandle = __SubscriptionHandleImpl<typeof REMOTE_MODULE>;

export class SubscriptionBuilder extends __SubscriptionBuilderImpl<typeof REMOTE_MODULE> {}

export class DbConnectionBuilder extends __DbConnectionBuilder<DbConnection> {}

export class DbConnection extends __DbConnectionImpl<typeof REMOTE_MODULE> {
  static builder = (): DbConnectionBuilder => {
    return new DbConnectionBuilder(REMOTE_MODULE, (config: __DbConnectionConfig<typeof REMOTE_MODULE>) => new DbConnection(config));
  };

  override subscriptionBuilder = (): SubscriptionBuilder => {
    return new SubscriptionBuilder(this);
  };
}
