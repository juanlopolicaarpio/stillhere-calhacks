// Hand-written SpacetimeDB client binding — see connection_sessions_table.ts
// for why. Arg order transcribed from specs/CONTRACTS_V2.md §2.
//
// NOTE: all reducers below except `demo_presence`-related ones are
// privileged — the module requires ctx.sender to be a seeded
// service_principal. A browser client calling these directly (without the
// backend's service identity) will be rejected with SenderError. The
// frontend never calls these; it only subscribes to their effects. They are
// included here for completeness of the typed client surface and so
// useSpacetime.ts can report "stdb.reducer_rejected" style errors honestly
// if ever attempted from a dev console.
import { t } from 'spacetimedb';

export default {
  sessionId: t.string(),
  patientRef: t.string(),
  contactRef: t.string(),
  mode: t.string(),
};
