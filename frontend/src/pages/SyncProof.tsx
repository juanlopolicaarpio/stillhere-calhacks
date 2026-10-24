import { useMemo, useState } from 'react';
import { Button, Card, Field, Input, PageHeader } from '../components/ui';
import { SimulatedBadge } from '../components/Badges';
import { StatusModesBar, useStatus } from '../components/StatusModesBar';
import { useSpacetime, stdbTimestampToDate } from '../hooks/useSpacetime';
import { useAgentEvents } from '../hooks/useAgentEvents';

function relativeTime(d: Date): string {
  const seconds = Math.max(0, Math.round((Date.now() - d.getTime()) / 1000));
  if (seconds < 2) return 'just now';
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  return d.toLocaleTimeString();
}

/**
 * Judge-facing proof that SpacetimeDB sync is real: two independently
 * subscribed browser contexts update live, with no polling and no reload.
 *
 * HONESTY RULE: every list below renders exactly the rows the SpacetimeDB
 * client cache holds via `useSpacetime()` (see
 * frontend/src/hooks/useSpacetime.ts) — nothing here is seeded, animated,
 * or replayed. An empty section means SpacetimeDB genuinely has no rows for
 * it yet. If the WebSocket is down, this page says so plainly and falls
 * back to the SSE feed instead of pretending SpacetimeDB is live.
 *
 * specs/CONTRACTS_V3.md §6: Agent K verified this with two independent
 * Node client processes (the Chrome extension needed for a live two-window
 * screenshot was not connected in this environment) — see
 * evidence/sync_proof_client_A.log, evidence/sync_proof_client_B.log, and
 * evidence/SYNC_PROOF_NOTES.md for the exact methodology and timestamps.
 * This page is the same client code path (`spacetimedb` SDK,
 * `useTable`/`useReducer` from `spacetimedb/react`) so a judge can repeat
 * that proof visually by opening this URL in two windows.
 */
export function SyncProof() {
  const stdb = useSpacetime();
  const { status } = useStatus();
  const sse = useAgentEvents();

  const [labelInput, setLabelInput] = useState('');
  const [presenceBusy, setPresenceBusy] = useState(false);
  const [presenceError, setPresenceError] = useState<string | null>(null);

  const defaultLabel = useMemo(
    () => `Window ${Math.random().toString(36).slice(2, 6)}`,
    [],
  );

  async function handleImHere() {
    setPresenceBusy(true);
    setPresenceError(null);
    const res = await stdb.touchPresence(labelInput.trim() || defaultLabel);
    if (!res.ok) setPresenceError(res.error ?? 'Unknown error calling upsert_demo_presence.');
    setPresenceBusy(false);
  }

  const presenceRows = [...stdb.demoPresence].sort(
    (a, b) => Number(b.lastSeen.microsSinceUnixEpoch - a.lastSeen.microsSinceUnixEpoch),
  );

  // Group live proposals into per-session revision chains (v1 -> v2 -> ...).
  type ProposalRow = (typeof stdb.planProposals)[number];
  const sessionsChrono = useMemo(() => {
    const bySession = new Map<string, ProposalRow[]>();
    for (const p of stdb.planProposals) {
      const list = bySession.get(p.sessionId) ?? [];
      list.push(p);
      bySession.set(p.sessionId, list);
    }
    for (const list of bySession.values()) list.sort((a, b) => a.version - b.version);
    return [...bySession.entries()].sort((a, b) => {
      const aMax = Math.max(...a[1].map((p) => Number(p.createdAt.microsSinceUnixEpoch)));
      const bMax = Math.max(...b[1].map((p) => Number(p.createdAt.microsSinceUnixEpoch)));
      return bMax - aMax;
    });
  }, [stdb.planProposals]);

  const transportLive = stdb.wsConnected && stdb.tablesReady;

  return (
    <div className="space-y-8">
      <PageHeader
        title="Two-client sync proof"
        subtitle="Open this exact URL in a second browser window side by side. Trigger a change in one — watch the other update with no refresh and no polling."
      />

      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <span
              className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold ${
                transportLive
                  ? 'bg-emerald-100 text-emerald-900 dark:bg-emerald-500/15 dark:text-emerald-100'
                  : 'bg-amber-100 text-amber-900 dark:bg-amber-500/15 dark:text-amber-100'
              }`}
            >
              <span aria-hidden>{transportLive ? '●' : '○'}</span>
              {transportLive
                ? 'Live transport: SpacetimeDB (direct WebSocket subscription)'
                : stdb.wsConnected
                  ? 'SpacetimeDB socket open, but tables not ready (schema mismatch) — not claiming it is live'
                  : 'SpacetimeDB unreachable — NOT claiming it is live'}
            </span>
            {!transportLive && (
              <span
                className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold ${
                  sse.connected
                    ? 'bg-sky-100 text-sky-900 dark:bg-sky-500/15 dark:text-sky-100'
                    : 'bg-stone-200 text-stone-700 dark:bg-stone-500/15 dark:text-stone-300'
                }`}
              >
                <span aria-hidden>{sse.connected ? '●' : '○'}</span>
                Fallback transport: SSE event stream {sse.connected ? 'connected' : 'not connected'}
              </span>
            )}
          </div>
          {stdb.connectionId && (
            <span className="text-xs" style={{ color: 'var(--sh-text-muted)' }}>
              this window's connection id: <code>{stdb.connectionId.slice(0, 16)}…</code>
            </span>
          )}
        </div>
        {stdb.connectionError && (
          <p className="mt-2 text-xs text-amber-700 dark:text-amber-300">
            Connection error: {stdb.connectionError}
          </p>
        )}
        <div className="mt-4">
          <StatusModesBar status={status} />
        </div>
      </Card>

      <Card>
        <h2 className="mb-2 text-lg font-semibold">1. Presence heartbeat — the simplest possible proof</h2>
        <p className="mb-4 text-sm" style={{ color: 'var(--sh-text-muted)' }}>
          Open this page in a second window. Type a label below and click "I'm here" in ONE
          window. The row appears in BOTH windows' lists below without either window reloading or
          polling — SpacetimeDB pushes the insert over the open WebSocket subscription.
        </p>
        <form
          className="mb-4 flex flex-wrap items-end gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            handleImHere();
          }}
        >
          <div className="flex-1" style={{ minWidth: '12rem' }}>
            <Field label="Label for this window">
              <Input
                value={labelInput}
                onChange={(e) => setLabelInput(e.target.value)}
                placeholder={defaultLabel}
              />
            </Field>
          </div>
          <Button type="submit" variant="primary" disabled={presenceBusy || !stdb.wsConnected}>
            {presenceBusy ? 'Sending…' : "I'm here"}
          </Button>
        </form>
        {!stdb.wsConnected && (
          <p className="mb-3 text-xs text-amber-700 dark:text-amber-300">
            SpacetimeDB is not connected right now, so this button cannot call the
            `upsert_demo_presence` reducer.
          </p>
        )}
        {presenceError && <p className="mb-3 text-xs text-red-600 dark:text-red-400">{presenceError}</p>}

        {presenceRows.length === 0 ? (
          <p className="text-sm" style={{ color: 'var(--sh-text-muted)' }}>
            No presence rows yet — this table is genuinely empty. Click "I'm here" above to add
            one.
          </p>
        ) : (
          <ul className="space-y-2">
            {presenceRows.map((row) => {
              const seen = stdbTimestampToDate(row.lastSeen);
              const isThisWindow = row.connectionId === stdb.connectionId;
              return (
                <li
                  key={row.connectionId}
                  className="flex items-center justify-between gap-3 rounded-xl border p-3 text-sm"
                  style={{ borderColor: 'var(--sh-border)' }}
                >
                  <span>
                    <span className="font-medium">{row.label}</span>{' '}
                    <code className="text-xs" style={{ color: 'var(--sh-text-muted)' }}>
                      {row.connectionId.slice(0, 10)}…
                    </code>
                    {isThisWindow && (
                      <span className="ml-2 rounded-full bg-[var(--sh-accent-soft)] px-2 py-0.5 text-xs text-[var(--sh-accent)]">
                        this window
                      </span>
                    )}
                  </span>
                  <span className="text-xs" style={{ color: 'var(--sh-text-muted)' }}>
                    {relativeTime(seen)}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      <Card>
        <h2 className="mb-2 text-lg font-semibold">2. Live plan proposals &amp; revision history</h2>
        <p className="mb-4 text-sm" style={{ color: 'var(--sh-text-muted)' }}>
          Each session's proposal versions are immutable rows. A counterproposal inserts a new
          version and marks the prior one inactive — that invalidates any approvals already
          recorded against the old version. Watch this update live from the judge dashboard's
          "Start outreach" / voice simulator in another window.
        </p>
        {sessionsChrono.length === 0 ? (
          <p className="text-sm" style={{ color: 'var(--sh-text-muted)' }}>
            No plan proposals yet in SpacetimeDB. Start outreach from the judge dashboard to see
            this fill in live.
          </p>
        ) : (
          <div className="space-y-6">
            {sessionsChrono.map(([sessionId, versions]) => {
              const consent = stdb.consentState.find((c) => c.sessionId === sessionId);
              return (
                <div key={sessionId} className="rounded-xl border p-4" style={{ borderColor: 'var(--sh-border)' }}>
                  <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                    <p className="text-sm font-semibold">
                      Session <code>{sessionId}</code>
                    </p>
                    {consent && (
                      <span
                        className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${
                          consent.revoked
                            ? 'bg-red-100 text-red-800 dark:bg-red-500/15 dark:text-red-200'
                            : 'bg-emerald-100 text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-200'
                        }`}
                      >
                        consent {consent.revoked ? `revoked (${consent.reason || 'no reason given'})` : 'active'}
                      </span>
                    )}
                  </div>
                  <ol className="space-y-3">
                    {versions.map((v) => {
                      const responses = stdb.participantResponses.filter(
                        (r) => r.proposalId === v.proposalId && r.version === v.version,
                      );
                      const confirmation = stdb.planConfirmations.find((c) => c.proposalId === v.proposalId);
                      const contactResponses = responses.filter((r) => r.role === 'contact');
                      return (
                        <li
                          key={v.proposalId}
                          className={`rounded-lg border p-3 text-sm ${v.active ? '' : 'opacity-60'}`}
                          style={{
                            borderColor: v.active ? 'var(--sh-accent)' : 'var(--sh-border)',
                          }}
                        >
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="font-semibold">v{v.version}</span>
                            {v.active ? (
                              <span className="rounded-full bg-[var(--sh-accent-soft)] px-2 py-0.5 text-xs text-[var(--sh-accent)]">
                                active
                              </span>
                            ) : (
                              <span className="rounded-full bg-stone-200 px-2 py-0.5 text-xs text-stone-700 dark:bg-stone-500/15 dark:text-stone-300">
                                superseded
                              </span>
                            )}
                            {v.supersedesVersion !== undefined && v.supersedesVersion !== null && (
                              <span className="text-xs" style={{ color: 'var(--sh-text-muted)' }}>
                                supersedes v{v.supersedesVersion}
                              </span>
                            )}
                            {confirmation ? (
                              <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-semibold text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-200">
                                CONFIRMED
                              </span>
                            ) : (
                              <span className="rounded-full bg-stone-200 px-2 py-0.5 text-xs text-stone-700 dark:bg-stone-500/15 dark:text-stone-300">
                                NOT BOOKED
                              </span>
                            )}
                          </div>
                          <p className="mt-1">
                            {v.activity}
                            {v.location && <> at {v.location}</>} — {v.startsAt}
                          </p>
                          {contactResponses.length > 1 && (
                            <p className="mt-1 text-xs" style={{ color: 'var(--sh-text-muted)' }}>
                              Group plan: {contactResponses.length} contact participants on this version.
                            </p>
                          )}
                          {responses.length === 0 ? (
                            <p className="mt-2 text-xs" style={{ color: 'var(--sh-text-muted)' }}>
                              No participant responses recorded for this version yet.
                            </p>
                          ) : (
                            <ul className="mt-2 space-y-1">
                              {responses.map((r, i) => (
                                <li key={r.responseId} className="flex items-center gap-2 text-xs">
                                  <span className="font-medium">
                                    {r.role === 'contact' && contactResponses.length > 1
                                      ? `contact #${i + 1}`
                                      : r.role}
                                  </span>
                                  <span>{r.response}</span>
                                  {r.simulated && <SimulatedBadge />}
                                </li>
                              ))}
                            </ul>
                          )}
                        </li>
                      );
                    })}
                  </ol>
                </div>
              );
            })}
          </div>
        )}
      </Card>
    </div>
  );
}
