import { useEffect, useRef, useState } from 'react';
import { ApiError, pitchOnboardingCall } from '../api/client';

export function CallNow() {
  const started = useRef(false);
  const [state, setState] = useState<'calling' | 'ringing' | 'error'>('calling');
  const [error, setError] = useState('');

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void pitchOnboardingCall()
      .then(() => setState('ringing'))
      .catch((err) => {
        setError(err instanceof ApiError ? err.message : 'The call could not be started.');
        setState('error');
      });
  }, []);

  return (
    <main className="flex min-h-[75vh] items-center justify-center px-5 text-center">
      <div className="max-w-xl">
        <div className={`mx-auto flex h-24 w-24 items-center justify-center rounded-3xl ${state === 'error' ? 'bg-rose-100' : 'bg-[var(--sh-accent-soft)] text-[var(--sh-accent)]'}`}>
          <span className="text-4xl" aria-hidden="true">{state === 'error' ? '!' : '☎'}</span>
        </div>
        <p className="mt-7 text-sm font-semibold uppercase tracking-[0.2em] text-[var(--sh-accent)]">Still Here</p>
        <h1 className="mt-3 text-4xl font-semibold">
          {state === 'calling' && 'Starting your call…'}
          {state === 'ringing' && 'Your phone is ringing.'}
          {state === 'error' && 'We couldn’t start the call.'}
        </h1>
        <p className="mt-4 text-lg" style={{ color: 'var(--sh-text-muted)' }}>
          {state === 'ringing' ? 'Answer to talk with Still Here. Everything else happens in the conversation.' : error || 'This takes just a moment.'}
        </p>
      </div>
    </main>
  );
}
