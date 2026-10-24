// Hand-written SpacetimeDB client binding — BEST-EFFORT / UNCONFIRMED.
//
// specs/CONTRACTS_V2.md §2 states the `demo_presence` table and says its
// "upsert may be called by any client" as the single named exception to the
// service_principals check, but the §2 reducer table never actually names a
// public writer reducer for it. This is a contract gap — reported to the
// lead (see this agent's final report). `upsert_presence(connectionId,
// label)` is this agent's best guess at the reducer the lead/agent B intend
// to add. useSpacetime.ts calls it defensively: if the published module
// does not define a reducer by this name, the call fails cleanly and the
// UI reports that honestly rather than fabricating presence data.
import { t } from 'spacetimedb';

export default {
  connectionId: t.string(),
  label: t.string(),
};
