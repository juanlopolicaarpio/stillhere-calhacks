// Hand-written SpacetimeDB client binding — see connection_sessions_table.ts
// for why. Transcribed from spacetime/spacetimedb/src/schema.ts.
//
// Exists to prove two independently-subscribed browser clients sync with no
// polling or reload (specs/CONTRACTS_V2.md §8 acceptance criterion 3).
import { t } from 'spacetimedb';

export default t.row({
  connectionId: t.string().primaryKey(),
  label: t.string(),
  lastSeen: t.timestamp(),
});
