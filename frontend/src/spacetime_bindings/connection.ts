// Builds (but does not open) the SpacetimeDB connection for the whole app.
// Created once at module scope, per the SDK's documented React pattern, so
// that <SpacetimeDBProvider> mounted once in main.tsx manages exactly one
// connection attempt. This module never throws on a missing/unreachable
// server — the SDK's ConnectionManager retries in the background and
// surfaces failure via connection state, which useSpacetime.ts reads.
import { DbConnection } from './index';

// Local SpacetimeDB server from specs/CONTRACTS_V2.md §0. Overridable for
// non-local demos. The frontend connects directly to SpacetimeDB — it does
// not proxy through the FastAPI backend, which only reports reachability
// via GET /api/status (coordination_mode / spacetimedb_reachable).
const SPACETIMEDB_URI = import.meta.env.VITE_SPACETIMEDB_URI ?? 'ws://127.0.0.1:3000';
const SPACETIMEDB_DB = import.meta.env.VITE_SPACETIMEDB_DB ?? 'stillhere';

export const spacetimeConnectionBuilder = DbConnection.builder()
  .withUri(SPACETIMEDB_URI)
  .withDatabaseName(SPACETIMEDB_DB)
  .withAutomaticReconnect({ maxDelayMs: 10_000 })
  .onConnectError((_ctx, error) => {
    // Honest console signal only — the UI reads connection state, not this.
    console.warn('[spacetimedb] connection error:', error?.message ?? error);
  });

export { SPACETIMEDB_URI, SPACETIMEDB_DB };
