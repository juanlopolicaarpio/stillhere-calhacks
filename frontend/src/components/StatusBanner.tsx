import { useEffect, useState } from 'react';
import { ApiError, getStatus, type StatusResponse, type VoiceMode } from '../api/client';
import { StatusModesBar } from './StatusModesBar';

/**
 * Persistent, honest banner. Reads GET /api/status and tells the truth about
 * voice_mode: 'simulated' means no real phone call is ever placed; only
 * 'cartesia_pstn' means real PSTN calling is configured.
 */
export function StatusBanner() {
  const [voiceMode, setVoiceMode] = useState<VoiceMode | null>(null);
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getStatus()
      .then((s) => {
        if (!cancelled) {
          setVoiceMode(s.voice_mode);
          setStatus(s);
        }
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : 'Could not reach the backend.');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) {
    return (
      <div className="w-full bg-stone-800 px-4 py-2 text-center text-xs font-medium text-stone-200">
        Could not reach the Still Here backend to confirm voice mode: {error}
      </div>
    );
  }

  if (!voiceMode) {
    return (
      <div className="w-full bg-stone-100 px-4 py-2 text-center text-xs text-stone-500 dark:bg-stone-800 dark:text-stone-400">
        Checking voice mode…
      </div>
    );
  }

  const isSimulated = voiceMode === 'simulated';

  return (
    <div
      className={`w-full px-4 py-2 text-center text-xs font-medium ${
        isSimulated
          ? 'bg-amber-100 text-amber-900 dark:bg-amber-500/15 dark:text-amber-100'
          : 'bg-emerald-100 text-emerald-900 dark:bg-emerald-500/15 dark:text-emerald-100'
      }`}
    >
      <div>
        {isSimulated ? (
          <>
            Voice mode: <strong>simulated</strong>. This build uses a local voice simulator — no
            real phone call is ever placed.
          </>
        ) : (
          <>
            Voice mode: <strong>cartesia_pstn</strong>. Real PSTN calling via Cartesia is
            configured — calls made from here are real.
          </>
        )}
      </div>
      <div className="mt-1.5 flex justify-center">
        <StatusModesBar status={status} />
      </div>
    </div>
  );
}
