import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import {
  ApiError,
  getPitchStatus,
  pitchJoshResponse,
  pitchRehearse,
  pitchReset,
  pitchSimulateThreeWeeks,
  pitchStart,
  type PitchStatus,
} from '../api/client';
import { SimulatedBadge } from '../components/Badges';
import { ErrorNotice } from '../components/ErrorNotice';
import { useStatus } from '../components/StatusModesBar';
import { Button, Card, PageHeader } from '../components/ui';
import { useAgentEvents } from '../hooks/useAgentEvents';
import { useLocalStorage } from '../hooks/useLocalStorage';

function when(value?: string | null) {
  if (!value) return 'Not proposed yet';
  return new Date(value).toLocaleString([], {
    weekday: 'long', hour: 'numeric', minute: '2-digit', month: 'short', day: 'numeric',
  });
}

function availabilityLabel(source?: string) {
  if (source === 'seeded_google_demo') return 'Google Calendar demo availability';
  if (source === 'seeded_outlook_demo') return 'Outlook demo availability';
  if (source === 'manual_demo') return 'Manual availability';
  return 'Patient availability';
}

function responseStyle(response?: string | null) {
  if (response === 'accepted') return 'bg-emerald-100 text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-200';
  if (response === 'declined') return 'bg-rose-100 text-rose-800 dark:bg-rose-500/15 dark:text-rose-200';
  return 'bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-200';
}

export function PitchDemo() {
  const { status: modes } = useStatus(5000);
  const [token, setToken] = useLocalStorage('stillhere_dashboard_token');
  const devToken = modes?.dev_dashboard_token ?? null;
  useEffect(() => {
    if (devToken && token !== devToken) setToken(devToken);
  }, [devToken, token, setToken]);
  const { events } = useAgentEvents(token || undefined);

  const [data, setData] = useState<PitchStatus | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      setData(await getPitchStatus());
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load pitch demo state.');
    }
  }

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), 2500);
    return () => window.clearInterval(id);
  }, []);
  useEffect(() => { void refresh(); }, [events.length]);

  async function run(label: string, action: () => Promise<unknown>) {
    setBusy(label);
    setError(null);
    try {
      await action();
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The demo action failed.');
    } finally {
      setBusy(null);
    }
  }

  const plan = data?.plan ?? null;
  const pitchEvents = useMemo(() => {
    const selected = plan ? events.filter((e) => !e.plan_id || e.plan_id === plan.id) : events;
    return selected.slice(-14).reverse();
  }, [events, plan]);
  const friends = data?.people.filter((p) => p.role === 'contact') ?? [];
  const patient = data?.people.find((p) => p.role === 'patient');
  const patientPart = plan?.participants.find((p) => p.role === 'patient');
  const allFriendsAccepted = plan
    ? plan.participants.filter((p) => p.role === 'contact').length === 3
      && plan.participants.filter((p) => p.role === 'contact').every((p) => p.response === 'accepted')
    : false;
  const opportunityReady = (data?.baseline?.deviation_ratio ?? 0) >= 1.6;
  const phase = plan?.status === 'COMPLETE'
    ? { label: 'Plan confirmed', copy: 'Everyone agreed. Still Here sent the final details.', step: 5 }
    : plan?.status === 'PATIENT_APPROVAL_PENDING'
      ? { label: 'Friends agreed — asking Josh', copy: 'The plan stays tentative until Josh says yes.', step: 4 }
      : plan
        ? { label: 'Calling friends and fitting schedules', copy: 'Still Here is collecting answers and adjusting one shared plan.', step: 3 }
        : opportunityReady
          ? { label: 'Reconnection opportunity found', copy: 'The real interaction-pattern rule fired. Still Here is ready to contact the circle.', step: 2 }
          : { label: 'Learning the group’s normal rhythm', copy: 'Approved memories, interactions, and availability build the baseline.', step: 1 };

  return (
    <div className="space-y-6" style={{
      '--sh-surface': '#fffdf8', '--sh-border': '#d8b98d', '--sh-accent': '#9a5b32',
      '--sh-accent-soft': '#f5e7d3', '--sh-text': '#472e1b', '--sh-text-muted': '#7c6044',
    } as CSSProperties}>
      <PageHeader
        title="The Connection Story"
        subtitle="Watch Still Here learn, notice, coordinate, and bring the group back together."
      />
      <ErrorNotice message={error} />

      <Card className="overflow-hidden text-amber-950">
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-amber-700">Right now</p>
        <div className="mt-2 flex flex-wrap items-end justify-between gap-5">
          <div className="max-w-2xl">
            <h2 className="text-3xl font-semibold">{phase.label}</h2>
            <p className="mt-2 text-amber-900">{phase.copy}</p>
          </div>
          <span className="rounded-full bg-amber-200/70 px-4 py-2 text-sm font-semibold text-amber-950">Step {phase.step} of 5</span>
        </div>
        <div className="mt-6 grid grid-cols-5 gap-2" aria-label={`Step ${phase.step} of 5`}>
          {['Learn', 'Detect', 'Coordinate', 'Ask Josh', 'Confirm'].map((label, index) => (
            <div key={label}>
              <div className={`h-2 rounded-full ${index < phase.step ? 'bg-amber-600' : 'bg-amber-200'}`} />
              <p className="mt-2 text-center text-[11px] text-amber-800">{label}</p>
            </div>
          ))}
        </div>
      </Card>

      <Card>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-amber-700">Run the story</p>
            <p className="mt-1 text-sm" style={{ color: 'var(--sh-text-muted)' }}>
              Move through the full story, or play it automatically from beginning to end.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" disabled={busy !== null} onClick={() => run('reset', pitchReset)}>
              {busy === 'reset' ? 'Resetting…' : 'Start over'}
            </Button>
            <Button variant="secondary" disabled={busy !== null} onClick={() => run('weeks', pitchSimulateThreeWeeks)}>
              {busy === 'weeks' ? 'Advancing…' : 'Jump ahead 3 weeks'}
            </Button>
            <Button variant="primary" disabled={busy !== null} onClick={() => run('live', () => pitchStart('cartesia_pstn'))}>
              {busy === 'live' ? 'Calling Maya…' : 'Call the friends'}
            </Button>
            <Button variant="secondary" disabled={busy !== null} onClick={() => run('rehearse', pitchRehearse)}>
              {busy === 'rehearse' ? 'Playing story…' : 'Play full demo'}
            </Button>
          </div>
        </div>
      </Card>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="p-5">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-amber-700">Josh’s circle</p>
          <h2 className="mt-1 text-xl font-semibold">Invitations accepted</h2>
          <div className="mt-4 space-y-3">
            {friends.map((friend) => (
              <div key={friend.id} className="flex items-center justify-between rounded-xl bg-emerald-50 px-4 py-3">
                <div className="flex items-center gap-3">
                  <span className="flex h-9 w-9 items-center justify-center rounded-full bg-emerald-200 font-semibold text-emerald-900">{friend.display_name[0]}</span>
                  <strong className="text-sm text-emerald-950">{friend.display_name}</strong>
                </div>
                <span className="text-xs font-semibold text-emerald-700">In the circle ✓</span>
              </div>
            ))}
          </div>
        </Card>

        <Card className="p-5">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-amber-700">New relationship memory</p>
          <div className="mt-5 flex items-center gap-4">
            <span className="text-5xl" aria-hidden="true">🎱</span>
            <div><p className="text-sm text-amber-800">Shared interest</p><h2 className="text-3xl font-semibold text-amber-950">Billiards</h2></div>
          </div>
          <p className="mt-5 text-sm font-medium text-amber-950">Josh · Maya · Daniel · Alex</p>
          <p className="mt-2 text-xs text-amber-700">Source: consented group-conversation scenario · saved with permission</p>
        </Card>

        <Card className="p-5">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-amber-700">Availability connected</p>
          <h2 className="mt-1 text-xl font-semibold">Three ways to share</h2>
          <div className="mt-4 space-y-3 text-sm">
            {[
              ['Maya', 'Google Calendar', 'bg-blue-100 text-blue-800'],
              ['Daniel', 'Outlook Calendar', 'bg-sky-100 text-sky-800'],
              ['Alex', 'Manual availability', 'bg-amber-100 text-amber-800'],
            ].map(([name, source, color]) => (
              <div key={name} className="flex items-center justify-between gap-3 rounded-xl border p-3" style={{ borderColor: 'var(--sh-border)' }}>
                <strong>{name}</strong><span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${color}`}>{source}</span>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <Card className="overflow-hidden p-0">
        <div className="grid lg:grid-cols-[0.78fr_1.22fr]">
          <div className="flex flex-col justify-between bg-[var(--sh-accent)] p-6 text-white sm:p-8">
            <div>
              <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.2em] text-amber-100">
                <span className="h-2 w-2 animate-pulse rounded-full bg-white" />
                Group conversation
              </div>
              <h2 className="mt-3 text-3xl font-semibold">Still Here is listening.</h2>
              <p className="mt-3 max-w-md text-sm leading-6 text-amber-50">
                The group is talking naturally while Still Here stays present in the conversation.
              </p>
            </div>
            <div className="mt-8">
              <div className="flex h-14 items-center gap-1" aria-label="Listening waveform">
                {[18, 34, 24, 46, 30, 52, 22, 40, 28, 48, 20, 36].map((height, index) => (
                  <span key={index} className="w-1.5 rounded-full bg-white/85" style={{ height }} />
                ))}
              </div>
              <p className="mt-3 text-xs font-medium text-amber-100">Listening only · nothing is saved without everyone’s permission</p>
            </div>
          </div>
          <div className="p-6 sm:p-8">
            <div className="flex items-center justify-between gap-4">
              <div className="flex -space-x-2">
                {['J', 'M', 'D', 'A'].map((initial, index) => (
                  <span key={initial} className="flex h-10 w-10 items-center justify-center rounded-full border-2 border-[var(--sh-surface)] bg-[var(--sh-accent-soft)] text-sm font-semibold text-[var(--sh-accent)]" style={{ zIndex: 4 - index }}>{initial}</span>
                ))}
              </div>
              <span className="rounded-full bg-[var(--sh-accent-soft)] px-3 py-1.5 text-xs font-semibold text-[var(--sh-accent)]">4 friends connected</span>
            </div>
            <div className="mt-6 space-y-3">
              <div className="mr-10 rounded-2xl rounded-tl-sm bg-[var(--sh-accent-soft)] px-4 py-3">
                <p className="text-xs font-semibold text-[var(--sh-accent)]">Daniel</p>
                <p className="mt-1 text-sm">We used to play billiards all the time.</p>
              </div>
              <div className="ml-10 rounded-2xl rounded-tr-sm border border-[var(--sh-border)] bg-white px-4 py-3">
                <p className="text-xs font-semibold text-[var(--sh-accent)]">Maya</p>
                <p className="mt-1 text-sm">Oh my gosh, yeah! Josh was always terrible at it.</p>
              </div>
              <div className="mr-5 rounded-2xl rounded-tl-sm bg-[var(--sh-accent)] px-4 py-3 text-white">
                <p className="text-xs font-semibold text-amber-100">Still Here</p>
                <p className="mt-1 text-sm">That sounds like a good time. Mind if I remember billiards as something your group enjoys?</p>
              </div>
            </div>
          </div>
        </div>
      </Card>

      {opportunityReady && (
        <div className="rounded-2xl border border-amber-300 bg-amber-50 px-6 py-5 text-center text-amber-950">
          <p className="text-xs font-semibold uppercase tracking-[0.3em] text-amber-700">Three weeks later</p>
          <h2 className="mt-2 text-2xl font-semibold">Their connection pattern changed.</h2>
          <p className="mt-1 text-sm">Earlier: regular interactions · Recently: fewer interactions · Opportunity identified: reconnect the circle.</p>
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {[
          ['Recorded interactions', data?.stats?.recorded_interactions ?? 0],
          ['Approved memories', data?.stats?.approved_memories ?? 0],
          ['Availability windows', data?.stats?.availability_windows ?? 0],
          ['Real phone calls', data?.stats?.real_calls ?? 0],
        ].map(([label, value]) => (
          <Card key={String(label)} className="p-5">
            <p className="text-3xl font-semibold text-amber-700">{value}</p>
            <p className="mt-1 text-sm" style={{ color: 'var(--sh-text-muted)' }}>{label}</p>
          </Card>
        ))}
      </div>

      <div className="grid gap-5 lg:grid-cols-[0.9fr_1.1fr]">
        <Card>
          <p className="text-xs font-semibold uppercase tracking-[0.18em]" style={{ color: 'var(--sh-text-muted)' }}>
            Connection signal
          </p>
          {data?.baseline ? (
            <>
              <div className="mt-3 flex items-end gap-3">
                <span className="text-4xl font-semibold">{data.baseline.current_gap_days?.toFixed(0)}</span>
                <span className="pb-1 text-sm" style={{ color: 'var(--sh-text-muted)' }}>
                  days since Maya · usual gap {data.baseline.median_gap_days?.toFixed(0)} days
                </span>
              </div>
              <div className={`mt-4 rounded-xl p-4 text-sm ${
                (data.baseline.deviation_ratio ?? 0) >= 1.6
                  ? 'bg-amber-100 text-amber-900 dark:bg-amber-500/15 dark:text-amber-100'
                  : 'bg-emerald-100 text-emerald-900 dark:bg-emerald-500/15 dark:text-emerald-100'
              }`}>
                <strong>{(data.baseline.deviation_ratio ?? 0) >= 1.6
                  ? 'Connection below baseline — reconnection opportunity.'
                  : 'Learning Josh and Maya’s normal rhythm.'}</strong>
                <div className="mt-1">Currently {data.baseline.deviation_ratio?.toFixed(1)}× the usual interval from {data.baseline.events_considered} recorded interactions.</div>
              </div>
              <p className="mt-3 text-xs" style={{ color: 'var(--sh-text-muted)' }}>Seeded, labeled interaction history; the comparison and trigger execute for real.</p>
              <div className="mt-5 flex h-28 items-end gap-3" aria-label="Four-week recorded interaction history">
                {(data.interaction_history ?? []).map((week) => {
                  const max = Math.max(1, ...(data.interaction_history ?? []).map((item) => item.count));
                  return (
                    <div key={week.label} className="flex h-full flex-1 flex-col items-center justify-end gap-1">
                      <span className="text-xs font-semibold">{week.count}</span>
                      <div className="w-full rounded-t-lg bg-amber-600" style={{ height: `${Math.max(8, (week.count / max) * 72)}px` }} />
                      <span className="text-[11px]" style={{ color: 'var(--sh-text-muted)' }}>{week.label}</span>
                    </div>
                  );
                })}
              </div>
            </>
          ) : <p className="mt-3 text-sm">Reset the story to generate the fictional interaction history.</p>}
        </Card>

        <Card>
          <div className="flex items-center justify-between gap-3">
            <div>
            <p className="text-xs font-semibold uppercase tracking-[0.18em]" style={{ color: 'var(--sh-text-muted)' }}>Plan being assembled</p>
              <h2 className="mt-2 text-2xl font-semibold">{plan?.activity ?? 'Waiting for the connection trigger'}</h2>
            </div>
            <span className="rounded-full bg-amber-200 px-3 py-1 text-xs font-semibold text-amber-900">{plan?.status.replaceAll('_', ' ') ?? 'READY'}</span>
          </div>
          <dl className="mt-5 grid gap-3 text-sm sm:grid-cols-2">
            <div><dt style={{ color: 'var(--sh-text-muted)' }}>When</dt><dd className="font-medium">{when(plan?.starts_at)}</dd></div>
            <div><dt style={{ color: 'var(--sh-text-muted)' }}>Where</dt><dd className="font-medium">{plan?.location ?? 'Not selected'}</dd></div>
          </dl>
          <p className="mt-4 rounded-xl bg-amber-100/70 p-3 text-sm text-amber-950">{plan?.rationale ?? 'Nothing is proposed until the recorded interaction pattern changes.'}</p>
        </Card>
      </div>

      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div><p className="text-xs font-semibold uppercase tracking-[0.18em] text-amber-700">What the AI remembers</p><h2 className="mt-1 text-xl font-semibold">Approved relationship memory</h2></div>
          <span className="rounded-full bg-emerald-100 px-3 py-1 text-xs font-semibold text-emerald-800">Source-backed · consent filtered</span>
        </div>
        <div className="mt-5 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {Array.from(new Map((data?.approved_memories ?? []).map((memory) => [memory.value, memory])).values()).slice(0, 9).map((memory, index) => (
            <div key={memory.value} className="rounded-xl border p-4" style={{ borderColor: 'var(--sh-border)' }}>
              <div className="flex justify-between gap-2"><strong>{index === 0 ? 'Shared interest' : 'Learned preference'}</strong><span className="text-xs" style={{ color: 'var(--sh-text-muted)' }}>{Math.round(memory.confidence * 100)}% confidence</span></div>
              <p className="mt-2 text-sm">{memory.value}</p>
              <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-amber-200"><div className="h-full w-[92%] rounded-full bg-amber-600" /></div>
              <p className="mt-1 text-xs" style={{ color: 'var(--sh-text-muted)' }}>Confirmed · source-backed · visible to planner</p>
            </div>
          ))}
        </div>
        <p className="mt-4 text-xs" style={{ color: 'var(--sh-text-muted)' }}>Clinical memories remain private and never enter friend-call context or SpacetimeDB’s public coordination tables.</p>
      </Card>

      {plan?.status === 'COMPLETE' && (
        <Card className="border-emerald-300 bg-emerald-50 text-center">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-emerald-700">Everyone said yes</p>
          <h2 className="mt-2 text-3xl font-semibold text-emerald-950">Sunday billiards is confirmed.</h2>
          <p className="mt-2 text-emerald-900">Josh, Maya, Daniel, and Alex received the agreed details.</p>
          <div className="mt-5 flex flex-wrap justify-center gap-3">
            <span className="rounded-full bg-white px-4 py-2 text-sm font-semibold text-emerald-800">Messages prepared ✓</span>
            <span className="rounded-full bg-white px-4 py-2 text-sm font-semibold text-emerald-800">Calendar invitations prepared ✓</span>
          </div>
        </Card>
      )}

      <Card>
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div><p className="text-xs font-semibold uppercase tracking-[0.18em] text-amber-700">Schedule fit</p><h2 className="mt-1 text-xl font-semibold">Why Sunday afternoon works</h2></div>
          <span className="rounded-full bg-emerald-100 px-3 py-1 text-xs font-semibold text-emerald-800">Shared window found</span>
        </div>
        <div className="mt-5 space-y-4">
          {[
            ['Josh', 'Available', 18, 82, 'bg-amber-700'],
            ['Maya', 'Sunday works', 25, 72, 'bg-amber-500'],
            ['Daniel', 'Must finish before 7 PM', 22, 58, 'bg-amber-500'],
            ['Alex', 'Available + dinner afterward', 15, 88, 'bg-emerald-500'],
          ].map(([name, note, left, right, color]) => (
            <div key={String(name)} className="grid grid-cols-[72px_1fr] items-center gap-3">
              <strong className="text-sm">{name}</strong>
              <div>
                <div className="relative h-4 overflow-hidden rounded-full bg-amber-200">
                  <div className={`absolute h-full rounded-full ${color}`} style={{ left: `${left}%`, right: `${100 - Number(right)}%` }} />
                </div>
                <p className="mt-1 text-xs" style={{ color: 'var(--sh-text-muted)' }}>{note}</p>
              </div>
            </div>
          ))}
        </div>
      </Card>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {friends.map((friend) => {
          const part = plan?.participants.find((p) => p.user_id === friend.id);
          const call = [...(data?.calls ?? [])].reverse().find((c) => c.contact_id === friend.id);
          const avail = data?.availability.find((a) => a.user_id === friend.id);
          return (
            <Card key={friend.id} className="p-5">
              <div className="flex items-start justify-between gap-2">
                <h3 className="text-lg font-semibold">{friend.display_name.split(' ')[0]}</h3>
                <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${responseStyle(part?.response)}`}>
                  {part?.response ?? call?.status ?? 'Pending'}
                </span>
              </div>
              <p className="mt-3 text-sm font-medium">
                {friend.id === 'pitch_maya' && 'Sunday works for billiards'}
                {friend.id === 'pitch_daniel' && 'Must leave by 7 PM'}
                {friend.id === 'pitch_alex' && 'Interested · suggested dinner afterward'}
              </p>
              <p className="mt-3 text-xs" style={{ color: 'var(--sh-text-muted)' }}>
                {availabilityLabel(avail?.source)}
              </p>
              {avail?.source.includes('seeded') && <div className="mt-2"><SimulatedBadge label="SEEDED AVAILABILITY" /></div>}
              {call && <p className="mt-3 text-xs">{call.mode === 'cartesia_pstn' ? 'Real Cartesia PSTN' : 'Local simulator'} · {call.status}</p>}
            </Card>
          );
        })}

        <Card className="p-5">
          <div className="flex items-start justify-between gap-2">
            <h3 className="text-lg font-semibold">{patient?.display_name.split(' ')[0] ?? 'Josh'}</h3>
            <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${responseStyle(patientPart?.response)}`}>
              {patientPart?.response ?? (allFriendsAccepted ? 'Awaiting invitation' : 'Waiting for friends')}
            </span>
          </div>
          <p className="mt-3 text-sm">Final approval is mandatory. The plan cannot confirm before Josh accepts identical terms.</p>
          <div className="mt-4 flex gap-2">
            <Button
              variant="primary"
              disabled={busy !== null || plan?.status !== 'PATIENT_APPROVAL_PENDING'}
              onClick={() => plan && run('approve', () => pitchJoshResponse(plan.id, 'accepted'))}
            >
              {busy === 'approve' ? 'Confirming…' : 'Josh accepts'}
            </Button>
          </div>
          <div className="mt-3"><SimulatedBadge label="SIMULATED JOSH APPROVAL" /></div>
        </Card>
      </div>

      <Card>
        <h2 className="text-lg font-semibold">What Still Here did</h2>
        <p className="mt-1 text-sm" style={{ color: 'var(--sh-text-muted)' }}>
          A simple, time-ordered record of what the assistant learned and acted on.
        </p>
        <div className="mt-4 space-y-2">
          {pitchEvents.length === 0 ? <p className="text-sm">Reset the story to begin.</p> : pitchEvents.map((event) => (
            <div key={event.id} className="flex items-start gap-3 rounded-xl border p-3 text-sm" style={{ borderColor: 'var(--sh-border)' }}>
              <span className="mt-0.5 text-xs" style={{ color: 'var(--sh-text-muted)' }}>{new Date(event.timestamp).toLocaleTimeString()}</span>
              <div className="min-w-0 flex-1"><p className="font-medium">{event.safe_summary}</p></div>
              {event.simulated && <SimulatedBadge />}
            </div>
          ))}
        </div>
      </Card>

      <div className="rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-950">
        <strong>Product boundary:</strong> {data?.positioning ?? 'Still Here supports social connection; it is not clinical monitoring or treatment.'}
      </div>
    </div>
  );
}
