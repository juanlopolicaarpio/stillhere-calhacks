import { useEffect, useState } from 'react';
import { ApiError, getStatus, type StatusResponse } from '../api/client';

/**
 * Compact row of mode badges for GET /api/status.
 *
 * HONESTY RULE: every field here is optional in StatusResponse because
 * other agents (G/H/J, per specs/CONTRACTS_V3.md) are adding
 * `calendar_mode` and `booking_mode` in parallel, and B/C already own
 * `coordination_mode`/`conversation_mode`. This component must never
 * crash or lie when a field is absent — it renders an explicit
 * "not reported" badge instead of guessing or hiding the row.
 */
function ModeBadge({ label, value, goodValues }: { label: string; value?: string; goodValues?: string[] }) {
  const known = value !== undefined && value !== null && value !== '';
  const isGood = known && (!goodValues || goodValues.includes(value));
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium ${
        !known
          ? 'border-stone-300/60 bg-stone-100 text-stone-500 dark:border-stone-500/30 dark:bg-stone-500/10 dark:text-stone-400'
          : isGood
            ? 'border-emerald-300/60 bg-emerald-50 text-emerald-800 dark:border-emerald-400/30 dark:bg-emerald-500/10 dark:text-emerald-200'
            : 'border-amber-300/60 bg-amber-50 text-amber-800 dark:border-amber-400/30 dark:bg-amber-500/10 dark:text-amber-200'
      }`}
      title={known ? `${label}: ${value}` : `${label}: not yet reported by GET /api/status`}
    >
      <span className="font-semibold">{label}:</span> {known ? value : 'not reported'}
    </span>
  );
}

export interface UseStatusResult {
  status: StatusResponse | null;
  error: string | null;
  loading: boolean;
}

export function useStatus(pollMs?: number): UseStatusResult {
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    function load() {
      getStatus()
        .then((s) => {
          if (cancelled) return;
          setStatus(s);
          setError(null);
          setLoading(false);
        })
        .catch((err) => {
          if (cancelled) return;
          setError(err instanceof ApiError ? err.message : 'Could not reach the backend.');
          setLoading(false);
        })
        .finally(() => {
          if (!cancelled && pollMs) timer = setTimeout(load, pollMs);
        });
    }
    load();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [pollMs]);

  return { status, error, loading };
}

export function StatusModesBar({ status }: { status: StatusResponse | null }) {
  if (!status) return null;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <ModeBadge label="voice" value={status.voice_mode} goodValues={['cartesia_pstn']} />
      <ModeBadge label="coordination" value={status.coordination_mode} goodValues={['spacetimedb']} />
      <ModeBadge label="conversation" value={status.conversation_mode} goodValues={['claude']} />
      <ModeBadge label="calendar" value={status.calendar_mode} goodValues={['google', 'microsoft']} />
      <ModeBadge label="booking" value={status.booking_mode} goodValues={[]} />
    </div>
  );
}
