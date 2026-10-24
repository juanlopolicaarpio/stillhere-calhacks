// Hand-written SpacetimeDB client binding — see connection_sessions_table.ts
// for why. Transcribed from spacetime/spacetimedb/src/schema.ts.
import { t } from 'spacetimedb';

export default t.row({
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
});
