// Standalone, non-React two-client sync proof for specs/CONTRACTS_V3.md §6.
//
// Agent K's task: prove that two INDEPENDENT SpacetimeDB clients, each with
// their own WebSocket connection and subscription, observe the SAME state
// changes with NO polling and NO reload. The Chrome extension needed for a
// real two-browser-window screenshot was not connected in this environment
// (`list_connected_browsers` returned `[]`), so this script performs the
// equivalent proof using the exact same client SDK and generated bindings
// that `frontend/src/hooks/useSpacetime.ts` uses in the browser
// (`spacetimedb` package, `DbConnection.builder()`,
// `connection.subscriptionBuilder().subscribe(...)`,
// `table.onInsert`/`onUpdate`). It is not a visual browser screenshot; it is
// a real, independent two-process subscription test against the live
// `stillhere` module.
//
// Usage: node --experimental-strip-types scripts/sync_proof_subscriber.ts <label>
//
// Run two copies (label "A" and "B") concurrently. Each prints a timestamped
// line for every row it observes arriving over its OWN socket — insert,
// update, or the initial subscription snapshot. Nothing here fabricates or
// replays data; if a table is empty, nothing is printed for it.
import { DbConnection, tables } from '../src/spacetime_bindings/index.ts';

const label = process.argv[2] ?? 'UNLABELED';
const uri = process.env.VITE_SPACETIMEDB_URI ?? 'ws://127.0.0.1:3000';
const db = process.env.VITE_SPACETIMEDB_DB ?? 'stillhere';

function ts(): string {
  return new Date().toISOString();
}

function log(msg: string): void {
  // eslint-disable-next-line no-console
  console.log(`[${ts()}] [client-${label}] ${msg}`);
}

log(`starting independent connection to ${uri} db=${db}`);

const builder = DbConnection.builder()
  .withUri(uri)
  .withDatabaseName(db)
  .onConnect((conn, identity) => {
    log(`CONNECTED. own identity=${identity.toHexString().slice(0, 16)}...`);
    conn.subscriptionBuilder()
      .onApplied(() => log('subscription applied (initial snapshot received)'))
      .onError((_ctx, err) => log(`subscription error: ${err?.message ?? err}`))
      .subscribe([
        'SELECT * FROM demo_presence',
        'SELECT * FROM plan_proposals',
        'SELECT * FROM participant_responses',
      ]);

    conn.db.demoPresence.onInsert((_ctx, row) => {
      log(`demo_presence INSERT connectionId=${row.connectionId.slice(0, 12)} label=${row.label}`);
    });
    conn.db.demoPresence.onUpdate((_ctx, _old, row) => {
      log(`demo_presence UPDATE connectionId=${row.connectionId.slice(0, 12)} label=${row.label}`);
    });

    conn.db.planProposals.onInsert((_ctx, row) => {
      log(
        `plan_proposals INSERT proposalId=${row.proposalId.slice(0, 8)} version=${row.version} ` +
          `active=${row.active} activity="${row.activity}" startsAt=${row.startsAt}`,
      );
    });
    conn.db.planProposals.onUpdate((_ctx, _old, row) => {
      log(
        `plan_proposals UPDATE proposalId=${row.proposalId.slice(0, 8)} version=${row.version} active=${row.active}`,
      );
    });

    conn.db.participantResponses.onInsert((_ctx, row) => {
      log(
        `participant_responses INSERT proposalId=${row.proposalId.slice(0, 8)} version=${row.version} ` +
          `role=${row.role} response=${row.response}`,
      );
    });
  })
  .onConnectError((_ctx, error) => {
    log(`CONNECT ERROR: ${error?.message ?? error}`);
  })
  .onDisconnect((_ctx, error) => {
    log(`DISCONNECTED: ${error?.message ?? 'clean'}`);
  });

builder.build();

// Keep the process alive to observe live updates; the harness running this
// script kills it after the proof window.
setInterval(() => {}, 1_000_000);
void tables; // referenced only to keep the import from being elided
