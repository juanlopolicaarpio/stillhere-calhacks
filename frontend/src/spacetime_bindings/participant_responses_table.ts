// Hand-written SpacetimeDB client binding — see connection_sessions_table.ts
// for why. Transcribed from spacetime/spacetimedb/src/schema.ts.
import { t } from 'spacetimedb';

export default t.row({
  responseId: t.string().primaryKey(),
  proposalId: t.string().index('btree'),
  version: t.u32(),
  role: t.string(), // 'patient' | 'contact'
  response: t.string(), // 'accepted' | 'declined' | 'counterproposed'
  agreedActivity: t.string(),
  agreedStartsAt: t.string(),
  simulated: t.bool(),
  respondedAt: t.timestamp(),
});
