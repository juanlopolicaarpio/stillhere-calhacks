// Hand-written SpacetimeDB client binding — see connection_sessions_table.ts
// for why. Transcribed from spacetime/spacetimedb/src/schema.ts.
//
// `payloadJson` arrives already redacted by backend/app/services/events.py —
// this table is append-only and the module does not re-validate redaction.
import { t } from 'spacetimedb';

export default t.row({
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
});
