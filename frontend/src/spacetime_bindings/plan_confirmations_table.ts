// Hand-written SpacetimeDB client binding — see connection_sessions_table.ts
// for why. Transcribed from spacetime/spacetimedb/src/schema.ts.
//
// Written ONLY by the `confirm_if_matching` reducer — there is no other
// writer, so a row appearing here is the one honest signal of a real
// confirmation (see specs/CONTRACTS_V2.md §3).
import { t } from 'spacetimedb';

export default t.row({
  confirmationId: t.string().primaryKey(),
  proposalId: t.string().unique(),
  version: t.u32(),
  activity: t.string(),
  startsAt: t.string(),
  idempotencyKey: t.string().unique(),
  confirmedAt: t.timestamp(),
});
