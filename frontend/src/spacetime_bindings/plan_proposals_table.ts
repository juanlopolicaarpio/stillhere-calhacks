// Hand-written SpacetimeDB client binding — see connection_sessions_table.ts
// for why. Transcribed from spacetime/spacetimedb/src/schema.ts.
//
// Rows here are IMMUTABLE: a revision inserts a new row with a higher
// `version` rather than mutating. `active === false` means superseded.
import { t } from 'spacetimedb';

export default t.row({
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
});
