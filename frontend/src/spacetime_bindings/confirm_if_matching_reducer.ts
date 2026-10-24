// Hand-written SpacetimeDB client binding — see start_session_reducer.ts.
// This is THE confirmation gate (specs/CONTRACTS_V2.md §3). The frontend
// never calls it directly; only the backend, holding the service identity,
// may.
import { t } from 'spacetimedb';

export default {
  proposalId: t.string(),
  version: t.u32(),
  idempotencyKey: t.string(),
};
