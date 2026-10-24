import { useEffect, useRef, useState } from 'react';
import { eventsStreamUrl, type AgentEvent } from '../api/client';

const MAX_EVENTS = 500;

export interface UseAgentEventsResult {
  events: AgentEvent[];
  connected: boolean;
  streamError: string | null;
}

/**
 * Subscribes to GET /api/events/stream via EventSource.
 *
 * HONESTY RULE: this hook renders ONLY events actually received from the
 * backend over SSE. It never fabricates, replays, or pre-seeds events. An
 * empty `events` array means the backend has not emitted anything yet —
 * callers must render that as a genuinely empty state, not a placeholder.
 *
 * `dashboardToken` is appended as a query param because the native
 * EventSource API cannot set the `X-Dashboard-Token` header that
 * specs/CONTRACTS.md documents for dashboard data routes. This is a known
 * gap — see the frontend agent's report to the lead.
 */
export function useAgentEvents(dashboardToken?: string): UseAgentEventsResult {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [streamError, setStreamError] = useState<string | null>(null);
  const sourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    setEvents([]);
    setConnected(false);
    setStreamError(null);

    let url = eventsStreamUrl();
    if (dashboardToken) {
      url += `?dashboard_token=${encodeURIComponent(dashboardToken)}`;
    }

    const source = new EventSource(url);
    sourceRef.current = source;

    source.onopen = () => {
      setConnected(true);
      setStreamError(null);
    };

    source.onmessage = (ev: MessageEvent<string>) => {
      if (!ev.data) return;
      try {
        const parsed = JSON.parse(ev.data) as AgentEvent;
        setEvents((prev) => {
          const next = [...prev, parsed];
          if (next.length > MAX_EVENTS) next.splice(0, next.length - MAX_EVENTS);
          return next;
        });
      } catch {
        // Malformed frame from the backend — do not fabricate a replacement.
        // Surface it as a stream error instead of silently dropping it.
        setStreamError('Received a malformed event frame from the server.');
      }
    };

    source.onerror = () => {
      setConnected(false);
      setStreamError('Lost connection to the live event stream.');
    };

    return () => {
      source.close();
      sourceRef.current = null;
    };
  }, [dashboardToken]);

  return { events, connected, streamError };
}
