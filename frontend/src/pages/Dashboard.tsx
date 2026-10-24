import { useEffect, useMemo, useState, type FormEvent } from 'react';
import {
  ApiError,
  demoReset,
  demoSamResponse,
  demoStart,
  getConsents,
  getMemories,
  getPlan,
  getRecentEvents,
  type AgentEvent,
  type Consent,
  type Memory,
  type PlanWithParticipants,
  type VoiceMode,
} from '../api/client';
import { Button, Card, Field, Input, PageHeader } from '../components/ui';
import { ErrorNotice } from '../components/ErrorNotice';
import { MockDataBadge, SimulatedBadge, VisibilityBadge } from '../components/Badges';
import { useAgentEvents } from '../hooks/useAgentEvents';
import { useLocalStorage } from '../hooks/useLocalStorage';
import { StatusModesBar, useStatus } from '../components/StatusModesBar';
import { Link } from 'react-router-dom';

function fmtTime(ts: string): string {
  try {
    return new Date(ts).toLocaleTimeString();
  } catch {
    return ts;
  }
}

function lastEventOfType(events: AgentEvent[], types: string[]): AgentEvent | undefined {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    if (types.includes(events[i].event_type)) return events[i];
  }
  return undefined;
}

const CALL_EVENT_TYPES = [
  'call.requested',
  'call.accepted_by_provider',
  'call.ringing',
  'call.answered',
  'call.first_audio',
  'call.turn',
  'call.ended',
  'call.failed',
];

export function Dashboard() {
  const [token, setToken] = useLocalStorage('stillhere_dashboard_token');
  const [tokenInput, setTokenInput] = useState(token);

  const { events, connected, streamError } = useAgentEvents(token || undefined);
  const { status: statusResp } = useStatus();

  const [recentEvents, setRecentEvents] = useState<AgentEvent[]>([]);
  const [recentError, setRecentError] = useState<string | null>(null);

  const [planId, setPlanId] = useLocalStorage('stillhere_demo_plan_id');
  const [callId, setCallId] = useLocalStorage('stillhere_demo_call_id');
  const [mode, setMode] = useState<VoiceMode | null>(null);

  const [plan, setPlan] = useState<PlanWithParticipants | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);

  const [consents, setConsents] = useState<Consent[]>([]);
  const [consentsError, setConsentsError] = useState<string | null>(null);

  const [memories, setMemories] = useState<Memory[]>([]);
  const [memoriesError, setMemoriesError] = useState<string | null>(null);

  const [controlError, setControlError] = useState<string | null>(null);
  const [resetting, setResetting] = useState(false);
  const [starting, setStarting] = useState(false);

  const [samResponse, setSamResponse] = useState<'accepted' | 'declined'>('accepted');
  const [samActivity, setSamActivity] = useState('');
  const [samStartsAt, setSamStartsAt] = useState('');
  const [samSubmitting, setSamSubmitting] = useState(false);
  const [samError, setSamError] = useState<string | null>(null);

  const dashOpts = token ? { dashboardToken: token } : null;

  // Seed with real history from /api/events/recent once a token is set.
  useEffect(() => {
    if (!dashOpts) return;
    setRecentError(null);
    getRecentEvents(200, dashOpts)
      .then(setRecentEvents)
      .catch((err) =>
        setRecentError(err instanceof ApiError ? err.message : 'Could not load recent events.'),
      );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  const allEvents = useMemo(() => {
    const byId = new Map<string, AgentEvent>();
    for (const e of recentEvents) byId.set(e.id, e);
    for (const e of events) byId.set(e.id, e);
    return Array.from(byId.values()).sort((a, b) => a.seq - b.seq);
  }, [recentEvents, events]);

  async function refreshPlan(id: string) {
    if (!dashOpts) return;
    try {
      const p = await getPlan(id, dashOpts);
      setPlan(p);
      setPlanError(null);
      return p;
    } catch (err) {
      setPlanError(err instanceof ApiError ? err.message : 'Could not load the plan.');
      return null;
    }
  }

  useEffect(() => {
    if (planId) refreshPlan(planId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [planId, token]);

  // Re-pull the plan whenever a plan/friend/patient event lands.
  const lastRelevantSeq = useMemo(() => {
    const relevant = allEvents.filter((e) =>
      [
        'plan.state_changed',
        'plan.confirmed',
        'plan.rejected',
        'plan.candidate_created',
        'friend.responded',
        'patient.responded',
        'notification.sent',
      ].includes(e.event_type),
    );
    return relevant.length ? relevant[relevant.length - 1].seq : null;
  }, [allEvents]);

  useEffect(() => {
    if (planId && lastRelevantSeq !== null) refreshPlan(planId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastRelevantSeq]);

  useEffect(() => {
    if (!plan || !dashOpts) return;
    getConsents(plan.patient_id)
      .then(setConsents)
      .catch((err) =>
        setConsentsError(err instanceof ApiError ? err.message : 'Could not load consent.'),
      );
    getMemories(plan.patient_id, 'demo')
      .then(setMemories)
      .catch((err) =>
        setMemoriesError(err instanceof ApiError ? err.message : 'Could not load memories.'),
      );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plan?.patient_id, plan?.version, token]);

  const consentForContact = plan ? consents.find((c) => c.contact_id === plan.contact_id) : undefined;

  async function handleReset() {
    if (!dashOpts) return;
    setResetting(true);
    setControlError(null);
    try {
      await demoReset(dashOpts);
      setPlanId('');
      setCallId('');
      setMode(null);
      setPlan(null);
      setConsents([]);
      setMemories([]);
    } catch (err) {
      setControlError(err instanceof ApiError ? err.message : 'Could not reset the demo.');
    } finally {
      setResetting(false);
    }
  }

  async function handleStart() {
    if (!dashOpts) return;
    setStarting(true);
    setControlError(null);
    try {
      const res = await demoStart({}, dashOpts);
      setPlanId(res.plan_id);
      setCallId(res.call_id);
      setMode(res.mode);
      await refreshPlan(res.plan_id);
    } catch (err) {
      setControlError(err instanceof ApiError ? err.message : 'Could not start outreach.');
    } finally {
      setStarting(false);
    }
  }

  async function handleSamSubmit(e: FormEvent) {
    e.preventDefault();
    if (!dashOpts || !planId) return;
    setSamSubmitting(true);
    setSamError(null);
    try {
      await demoSamResponse(
        {
          plan_id: planId,
          response: samResponse,
          activity: samActivity || undefined,
          starts_at: samStartsAt || undefined,
        },
        dashOpts,
      );
      await refreshPlan(planId);
    } catch (err) {
      setSamError(err instanceof ApiError ? err.message : 'Could not submit Sam’s response.');
    } finally {
      setSamSubmitting(false);
    }
  }

  if (!token) {
    return (
      <div className="mx-auto max-w-md">
        <PageHeader title="Judge dashboard" subtitle="Enter the dashboard token to view live demo data." />
        <Card>
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              setToken(tokenInput.trim());
            }}
          >
            <Field label="Dashboard token">
              <Input
                value={tokenInput}
                onChange={(e) => setTokenInput(e.target.value)}
                placeholder="X-Dashboard-Token value"
              />
            </Field>
            <Button type="submit" variant="primary" className="w-full">
              Continue
            </Button>
          </form>
        </Card>
      </div>
    );
  }

  const callEventsForCall = callId
    ? allEvents.filter((e) => e.call_id === callId && CALL_EVENT_TYPES.includes(e.event_type))
    : allEvents.filter((e) => CALL_EVENT_TYPES.includes(e.event_type));
  const latestCallEvent = callEventsForCall[callEventsForCall.length - 1];

  const toolEvents = (callId ? allEvents.filter((e) => e.call_id === callId) : allEvents).filter(
    (e) => e.event_type === 'call.tool_invoked',
  );

  const friendResponseEvent = lastEventOfType(allEvents, ['friend.responded']);
  const notificationEvents = allEvents.filter((e) => e.event_type === 'notification.sent');

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <PageHeader title="Judge dashboard" subtitle="Live, real data only — nothing on this page is scripted." />
        <div className="flex items-center gap-2">
          <span
            className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium ${
              connected
                ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-200'
                : 'bg-stone-200 text-stone-700 dark:bg-stone-500/15 dark:text-stone-300'
            }`}
          >
            <span aria-hidden>{connected ? '●' : '○'}</span>
            {connected ? 'Live event stream connected' : 'Event stream disconnected'}
          </span>
          <Button variant="secondary" onClick={() => setToken('')}>
            Change token
          </Button>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <StatusModesBar status={statusResp} />
        <Link to="/sync-proof" className="text-sm font-medium" style={{ color: 'var(--sh-accent)' }}>
          View two-client SpacetimeDB sync proof →
        </Link>
      </div>

      <ErrorNotice message={streamError} />
      <ErrorNotice message={recentError} />

      <div className="flex flex-wrap gap-3">
        <Button variant="secondary" disabled={resetting} onClick={handleReset}>
          {resetting ? 'Resetting…' : 'Reset demo'}
        </Button>
        <Button variant="primary" disabled={starting} onClick={handleStart}>
          {starting ? 'Starting…' : 'Start outreach'}
        </Button>
        {mode && (
          <span
            className={`inline-flex items-center rounded-full px-3 py-1 text-xs font-semibold ${
              mode === 'simulated'
                ? 'bg-amber-100 text-amber-900 dark:bg-amber-500/15 dark:text-amber-100'
                : 'bg-emerald-100 text-emerald-900 dark:bg-emerald-500/15 dark:text-emerald-100'
            }`}
          >
            mode: {mode}
          </span>
        )}
      </div>
      <ErrorNotice message={controlError} />

      <div className="grid gap-8 lg:grid-cols-2">
        {/* LEFT COLUMN */}
        <div className="space-y-6">
          <Card>
            <h2 className="mb-3 text-lg font-semibold">Sam</h2>
            {plan ? (
              <div className="space-y-2 text-sm">
                <p>
                  <span style={{ color: 'var(--sh-text-muted)' }}>Patient id:</span>{' '}
                  <code>{plan.patient_id}</code>
                </p>
                <p>
                  <span style={{ color: 'var(--sh-text-muted)' }}>Selected relationship / contact id:</span>{' '}
                  <code>{plan.contact_id}</code>
                </p>
                <div>
                  <span style={{ color: 'var(--sh-text-muted)' }}>Consent indicator: </span>
                  {consentForContact ? (
                    <span
                      className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${
                        consentForContact.active
                          ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-200'
                          : 'bg-stone-200 text-stone-600'
                      }`}
                    >
                      {consentForContact.active ? 'Active consent on file' : 'Consent inactive'}
                    </span>
                  ) : (
                    <span className="text-xs" style={{ color: 'var(--sh-text-muted)' }}>
                      No consent record found for this contact
                    </span>
                  )}
                  <ErrorNotice message={consentsError} />
                </div>
              </div>
            ) : (
              <p className="text-sm" style={{ color: 'var(--sh-text-muted)' }}>
                No plan started yet. Use "Start outreach" above.
              </p>
            )}
          </Card>

          <Card>
            <h2 className="mb-3 text-lg font-semibold">Participants</h2>
            {plan && plan.participants.length > 0 ? (
              <>
                {plan.participants.filter((p) => p.role === 'contact').length > 1 && (
                  <p className="mb-2 text-xs font-medium" style={{ color: 'var(--sh-accent)' }}>
                    Group plan — {plan.participants.filter((p) => p.role === 'contact').length}{' '}
                    contact participants.
                  </p>
                )}
                <ul className="space-y-2 text-sm">
                  {plan.participants.map((p, i) => {
                    const isContact = p.role === 'contact';
                    const contactIdx = plan.participants
                      .filter((q) => q.role === 'contact')
                      .indexOf(p);
                    return (
                      <li
                        key={`${p.role}-${p.user_id}-${i}`}
                        className="flex items-center justify-between gap-2 rounded-xl border p-3"
                        style={{ borderColor: 'var(--sh-border)' }}
                      >
                        <span className="font-medium">
                          {isContact
                            ? plan.participants.filter((q) => q.role === 'contact').length > 1
                              ? `Contact #${contactIdx + 1}`
                              : 'Contact'
                            : 'Sam (patient)'}
                        </span>
                        <span className="flex items-center gap-2">
                          {p.response ? (
                            <span
                              className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${
                                p.response === 'accepted'
                                  ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-200'
                                  : 'bg-stone-200 text-stone-700 dark:bg-stone-500/15 dark:text-stone-300'
                              }`}
                            >
                              {p.response}
                            </span>
                          ) : (
                            <span className="text-xs" style={{ color: 'var(--sh-text-muted)' }}>
                              no response yet
                            </span>
                          )}
                          {p.simulated && <SimulatedBadge />}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              </>
            ) : (
              <p className="text-sm" style={{ color: 'var(--sh-text-muted)' }}>
                No participants yet.
              </p>
            )}
          </Card>

          <Card>
            <h2 className="mb-3 text-lg font-semibold">Why this plan?</h2>
            {plan?.rationale ? (
              <p className="text-sm leading-relaxed">{plan.rationale}</p>
            ) : (
              <p className="text-sm" style={{ color: 'var(--sh-text-muted)' }}>
                No rationale available yet.
              </p>
            )}
            {plan && (
              <p className="mt-3 text-sm">
                <span style={{ color: 'var(--sh-text-muted)' }}>Proposed activity:</span>{' '}
                {plan.activity || '—'}
                {plan.location && <> at {plan.location}</>}
              </p>
            )}
          </Card>

          <Card>
            <h2 className="mb-3 text-lg font-semibold">Retrieved memories</h2>
            <ErrorNotice message={memoriesError} />
            {memories.length === 0 ? (
              <p className="text-sm" style={{ color: 'var(--sh-text-muted)' }}>
                No demo-visible memories retrieved yet.
              </p>
            ) : (
              <ul className="space-y-3">
                {memories.map((m) => (
                  <li key={m.id} className="rounded-xl border p-3 text-sm" style={{ borderColor: 'var(--sh-border)' }}>
                    <div className="flex items-start justify-between gap-2">
                      <p className="font-medium">{m.value}</p>
                      <VisibilityBadge visibility={m.visibility} />
                    </div>
                    {m.source_quote && (
                      <p className="mt-1 text-xs italic" style={{ color: 'var(--sh-text-muted)' }}>
                        source: “{m.source_quote}”
                      </p>
                    )}
                    {plan?.memory_citations?.includes(m.id) && (
                      <p className="mt-1 text-xs font-medium" style={{ color: 'var(--sh-accent)' }}>
                        cited in current plan
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card>
            <h2 className="mb-3 text-lg font-semibold">Availability</h2>
            {plan ? (
              <div className="text-sm">
                <p>
                  <span style={{ color: 'var(--sh-text-muted)' }}>Proposed time:</span>{' '}
                  {plan.starts_at ? new Date(plan.starts_at).toLocaleString() : 'not yet proposed'}
                </p>
                <p>
                  <span style={{ color: 'var(--sh-text-muted)' }}>Status:</span> {plan.status}
                </p>
              </div>
            ) : (
              <p className="text-sm" style={{ color: 'var(--sh-text-muted)' }}>
                No availability data yet.
              </p>
            )}
          </Card>
        </div>

        {/* RIGHT COLUMN */}
        <div className="space-y-6">
          <Card>
            <h2 className="mb-3 text-lg font-semibold">Live call status</h2>
            {latestCallEvent ? (
              <div className="text-sm">
                <p className="flex items-center gap-2">
                  <span className="font-medium">{latestCallEvent.event_type}</span>
                  {latestCallEvent.simulated && <SimulatedBadge />}
                </p>
                <p style={{ color: 'var(--sh-text-muted)' }}>{latestCallEvent.safe_summary}</p>
                <p className="text-xs" style={{ color: 'var(--sh-text-muted)' }}>
                  {fmtTime(latestCallEvent.timestamp)}
                </p>
              </div>
            ) : (
              <p className="text-sm" style={{ color: 'var(--sh-text-muted)' }}>
                No call events yet.
              </p>
            )}
          </Card>

          <Card>
            <h2 className="mb-3 text-lg font-semibold">Tool-call timeline</h2>
            {toolEvents.length === 0 ? (
              <p className="text-sm" style={{ color: 'var(--sh-text-muted)' }}>
                No tool calls yet.
              </p>
            ) : (
              <ul className="space-y-2">
                {toolEvents.map((e) => {
                  const payload = (e.payload ?? {}) as Record<string, unknown>;
                  const isMockShowtimes = payload.source === 'mock';
                  return (
                    <li key={e.id} className="rounded-xl border p-3 text-sm" style={{ borderColor: 'var(--sh-border)' }}>
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-medium">{e.safe_summary}</span>
                        <span className="flex items-center gap-1">
                          {isMockShowtimes && <MockDataBadge />}
                          {e.simulated && <SimulatedBadge />}
                        </span>
                      </div>
                      <p className="text-xs" style={{ color: 'var(--sh-text-muted)' }}>
                        {fmtTime(e.timestamp)}
                      </p>
                    </li>
                  );
                })}
              </ul>
            )}
          </Card>

          <Card>
            <h2 className="mb-3 text-lg font-semibold">Friend response</h2>
            {friendResponseEvent ? (
              <div className="text-sm">
                <p className="flex items-center gap-2">
                  {friendResponseEvent.safe_summary}
                  {friendResponseEvent.simulated && <SimulatedBadge />}
                </p>
                <p className="text-xs" style={{ color: 'var(--sh-text-muted)' }}>
                  {fmtTime(friendResponseEvent.timestamp)}
                </p>
              </div>
            ) : (
              <p className="text-sm" style={{ color: 'var(--sh-text-muted)' }}>
                No friend response yet.
              </p>
            )}
          </Card>

          <Card>
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-lg font-semibold">Sam approval simulator</h2>
              <SimulatedBadge label="SIMULATED SAM APPROVAL" />
            </div>
            <form className="space-y-3" onSubmit={handleSamSubmit}>
              <div className="flex gap-3">
                {(['accepted', 'declined'] as const).map((opt) => (
                  <label
                    key={opt}
                    className={`cursor-pointer rounded-xl border px-4 py-2 text-sm ${
                      samResponse === opt ? 'border-[var(--sh-accent)] text-[var(--sh-accent)]' : ''
                    }`}
                    style={{ borderColor: samResponse === opt ? undefined : 'var(--sh-border)' }}
                  >
                    <input
                      type="radio"
                      name="sam-response"
                      className="sr-only"
                      checked={samResponse === opt}
                      onChange={() => setSamResponse(opt)}
                    />
                    {opt}
                  </label>
                ))}
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Override activity (optional)">
                  <Input value={samActivity} onChange={(e) => setSamActivity(e.target.value)} />
                </Field>
                <Field label="Override start time (optional)">
                  <Input
                    type="datetime-local"
                    value={samStartsAt}
                    onChange={(e) => setSamStartsAt(e.target.value)}
                  />
                </Field>
              </div>
              <ErrorNotice message={samError} />
              <Button type="submit" variant="primary" disabled={samSubmitting || !planId}>
                {samSubmitting ? 'Submitting…' : 'Submit Sam’s simulated response'}
              </Button>
              {!planId && (
                <p className="text-xs" style={{ color: 'var(--sh-text-muted)' }}>
                  Start outreach first to get a plan to respond to.
                </p>
              )}
            </form>
          </Card>

          <Card>
            <h2 className="mb-3 text-lg font-semibold">Confirmed plan</h2>
            {plan && (plan.status === 'PLAN_CONFIRMED' || plan.status === 'NOTIFICATIONS_SENT' || plan.status === 'COMPLETE') ? (
              <div className="text-sm">
                <p className="font-medium">{plan.activity}</p>
                <p style={{ color: 'var(--sh-text-muted)' }}>
                  {plan.location} {plan.starts_at && `· ${new Date(plan.starts_at).toLocaleString()}`}
                </p>
                {plan.confirmed_at && (
                  <p className="text-xs" style={{ color: 'var(--sh-text-muted)' }}>
                    Confirmed at {new Date(plan.confirmed_at).toLocaleString()}
                  </p>
                )}
              </div>
            ) : (
              <p className="text-sm" style={{ color: 'var(--sh-text-muted)' }}>
                Not confirmed yet{plan ? ` (current status: ${plan.status})` : ''}.
              </p>
            )}
            <ErrorNotice message={planError} />
          </Card>

          <Card>
            <h2 className="mb-3 text-lg font-semibold">Notification status</h2>
            {notificationEvents.length === 0 ? (
              <p className="text-sm" style={{ color: 'var(--sh-text-muted)' }}>
                No notifications sent yet.
              </p>
            ) : (
              <ul className="space-y-2">
                {notificationEvents.map((e) => (
                  <li key={e.id} className="rounded-xl border p-3 text-sm" style={{ borderColor: 'var(--sh-border)' }}>
                    <div className="flex items-center justify-between gap-2">
                      <span>{e.safe_summary}</span>
                      {e.simulated && <SimulatedBadge />}
                    </div>
                    <p className="text-xs" style={{ color: 'var(--sh-text-muted)' }}>
                      {fmtTime(e.timestamp)}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
