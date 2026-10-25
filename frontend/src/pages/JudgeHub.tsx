import { useState, type CSSProperties } from 'react';
import { qrPngUrl } from '../api/client';
import { Card } from '../components/ui';
import { PitchDemo } from './PitchDemo';

export function JudgeHub() {
  const [view, setView] = useState<'onboarding' | 'brain'>('onboarding');
  return (
    <div className="space-y-6" style={{
      '--sh-surface': '#fffdf8', '--sh-border': '#d8b98d', '--sh-accent': '#9a5b32',
      '--sh-accent-soft': '#f5e7d3', '--sh-text': '#472e1b', '--sh-text-muted': '#7c6044',
    } as CSSProperties}>
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <p className="text-sm font-semibold uppercase tracking-[0.2em] text-[var(--sh-accent)]">The connection story</p>
          <h1 className="mt-1 text-3xl font-semibold">See Still Here at work</h1>
          <p className="mt-1 text-sm" style={{ color: 'var(--sh-text-muted)' }}>One connection story, unfolding in real time.</p>
        </div>
        <div className="flex rounded-full border p-1" style={{ borderColor: 'var(--sh-border)' }}>
          <button onClick={() => setView('onboarding')} className={`rounded-full px-5 py-2 text-sm font-semibold ${view === 'onboarding' ? 'bg-[var(--sh-accent)] text-white' : ''}`}>1. Scan &amp; Call</button>
          <button onClick={() => setView('brain')} className={`rounded-full px-5 py-2 text-sm font-semibold ${view === 'brain' ? 'bg-[var(--sh-accent)] text-white' : ''}`}>2. Live Agent Brain</button>
        </div>
      </div>

      {view === 'onboarding' ? (
        <Card className="mx-auto flex min-h-[620px] max-w-3xl flex-col items-center justify-center text-center">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[var(--sh-accent)]">One scan · one conversation</p>
          <h2 className="mt-3 text-4xl font-semibold">Scan. Then your phone rings.</h2>
          <p className="mt-3 max-w-xl text-base" style={{ color: 'var(--sh-text-muted)' }}>
            No forms, menus, or web onboarding. The entire conversation happens naturally by phone.
          </p>
          <img src={qrPngUrl(`${window.location.origin}/call-now`)} alt="QR code to receive a Still Here call" className="mt-8 h-72 w-72 rounded-3xl border bg-white p-4" />
        </Card>
      ) : <PitchDemo />}
    </div>
  );
}
