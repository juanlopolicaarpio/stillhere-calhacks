import { useState } from 'react';
import { Link } from 'react-router-dom';
import { qrPngUrl } from '../api/client';
import { Button, Card } from '../components/ui';

export function Landing() {
  const [qrFailed, setQrFailed] = useState(false);

  return (
    <div className="grid gap-10 md:grid-cols-[1.1fr_0.9fr] md:items-start">
      <div>
        <p className="mb-3 text-sm font-semibold uppercase tracking-wide" style={{ color: 'var(--sh-accent)' }}>
          Still Here
        </p>
        <h1 className="text-4xl font-semibold leading-tight tracking-tight sm:text-5xl" style={{ color: 'var(--sh-text)' }}>
          A gentle companion for staying connected to the people you love.
        </h1>
        <p className="mt-6 max-w-xl text-lg leading-relaxed" style={{ color: 'var(--sh-text-muted)' }}>
          Still Here is an opt-in AI companion for adults after a psychiatric discharge. It talks
          with you, remembers what matters to you, and — only with your explicit permission —
          helps reach out to the friends and family you choose, to plan small moments of
          connection.
        </p>

        <Card className="mt-8 max-w-xl">
          <ul className="space-y-2 text-sm" style={{ color: 'var(--sh-text-muted)' }}>
            <li>• For adults 18 and older only.</li>
            <li>• Entirely voluntary. You can stop at any time.</li>
            <li>• No diagnosis, prescription, or clinical referral is required or provided.</li>
            <li>• Not a medical device and not a substitute for care from a clinician.</li>
          </ul>
        </Card>

        <div className="mt-8 flex flex-wrap gap-3">
          <Link to="/signup">
            <Button variant="primary">Get started</Button>
          </Link>
          <Link to="/dashboard">
            <Button variant="secondary">Judge / demo dashboard</Button>
          </Link>
        </div>
      </div>

      <Card className="flex flex-col items-center gap-4 text-center">
        <h2 className="text-lg font-semibold">Scan to start</h2>
        {qrFailed ? (
          <div
            className="flex h-56 w-56 items-center justify-center rounded-xl border text-center text-sm"
            style={{ borderColor: 'var(--sh-border)', color: 'var(--sh-text-muted)' }}
          >
            Could not load the QR code from the backend.
          </div>
        ) : (
          <img
            src={qrPngUrl()}
            alt="QR code that opens the Still Here sign-up page"
            className="h-56 w-56 rounded-xl border bg-white object-contain p-2"
            style={{ borderColor: 'var(--sh-border)' }}
            onError={() => setQrFailed(true)}
          />
        )}
        <p className="text-sm" style={{ color: 'var(--sh-text-muted)' }}>
          Scan with your phone's camera to open the sign-up page.
        </p>
        <p className="text-xs leading-relaxed" style={{ color: 'var(--sh-text-muted)' }}>
          Scanning this code only opens the sign-up page on your phone. It does not authorize
          any call, text, or outreach of any kind — that only happens after you complete sign-up
          and separately activate outreach.
        </p>
      </Card>
    </div>
  );
}
