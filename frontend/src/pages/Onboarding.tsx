import { useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ApiError, postOnboardingPreferences } from '../api/client';
import { Button, Card, Field, Input, PageHeader } from '../components/ui';
import { ErrorNotice } from '../components/ErrorNotice';
import { useLocalStorage } from '../hooks/useLocalStorage';

const ACTIVITY_OPTIONS = [
  'Coffee or a meal',
  'A walk outside',
  'Phone or video calls',
  'Watching a movie together',
  'Playing games',
  'Quiet time, just company',
];

function detectTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone;
  } catch {
    return '';
  }
}

export function Onboarding() {
  const navigate = useNavigate();
  const [userId] = useLocalStorage('stillhere_user_id');
  const [displayName] = useLocalStorage('stillhere_display_name');

  const [timezone, setTimezone] = useState(detectTimezone());
  const [activities, setActivities] = useState<string[]>([]);
  const [quietStart, setQuietStart] = useState('21:00');
  const [quietEnd, setQuietEnd] = useState('09:00');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  function toggleActivity(option: string) {
    setActivities((prev) =>
      prev.includes(option) ? prev.filter((a) => a !== option) : [...prev, option],
    );
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!userId) return;
    setSubmitting(true);
    setError(null);
    try {
      await postOnboardingPreferences({
        user_id: userId,
        timezone: timezone || undefined,
        activity_preferences: activities.length ? activities : undefined,
        quiet_hours_start: quietStart || undefined,
        quiet_hours_end: quietEnd || undefined,
      });
      setDone(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save your preferences.');
    } finally {
      setSubmitting(false);
    }
  }

  if (!userId) {
    return (
      <div className="mx-auto max-w-lg">
        <PageHeader title="Let's get to know you a little" />
        <Card>
          <p className="text-sm" style={{ color: 'var(--sh-text-muted)' }}>
            We don't have a signed-up account in this browser yet.{' '}
            <Link to="/signup" className="font-semibold" style={{ color: 'var(--sh-accent)' }}>
              Sign up first
            </Link>
            , then come back here.
          </p>
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-lg">
      <PageHeader
        title={displayName ? `A little about you, ${displayName}` : 'A little about you'}
        subtitle="No wrong answers — this just helps your companion fit your life."
      />

      <Card>
        {done ? (
          <div className="space-y-5 text-center">
            <p className="text-lg font-medium">Thank you — that's saved.</p>
            <p className="text-sm" style={{ color: 'var(--sh-text-muted)' }}>
              Next, let's build your circle of trusted people.
            </p>
            <Button variant="primary" onClick={() => navigate('/circle')}>
              Go to my circle
            </Button>
          </div>
        ) : (
          <form className="space-y-6" onSubmit={handleSubmit}>
            <div>
              <p className="mb-3 text-sm font-medium">
                What kinds of moments with people would feel good to have more of?
              </p>
              <div className="grid gap-2 sm:grid-cols-2">
                {ACTIVITY_OPTIONS.map((option) => (
                  <label
                    key={option}
                    className={`cursor-pointer rounded-xl border px-4 py-2.5 text-sm ${
                      activities.includes(option) ? 'border-[var(--sh-accent)] text-[var(--sh-accent)]' : ''
                    }`}
                    style={{ borderColor: activities.includes(option) ? undefined : 'var(--sh-border)' }}
                  >
                    <input
                      type="checkbox"
                      className="sr-only"
                      checked={activities.includes(option)}
                      onChange={() => toggleActivity(option)}
                    />
                    {option}
                  </label>
                ))}
              </div>
            </div>

            <Field label="Your timezone">
              <Input value={timezone} onChange={(e) => setTimezone(e.target.value)} placeholder="America/Los_Angeles" />
            </Field>

            <div className="grid grid-cols-2 gap-4">
              <Field label="Quiet hours start">
                <Input type="time" value={quietStart} onChange={(e) => setQuietStart(e.target.value)} />
              </Field>
              <Field label="Quiet hours end">
                <Input type="time" value={quietEnd} onChange={(e) => setQuietEnd(e.target.value)} />
              </Field>
            </div>
            <p className="text-xs" style={{ color: 'var(--sh-text-muted)' }}>
              We won't suggest calls or texts to your contacts during your quiet hours.
            </p>

            <ErrorNotice message={error} />

            <Button type="submit" variant="primary" disabled={submitting} className="w-full">
              {submitting ? 'Saving…' : 'Save and continue'}
            </Button>
          </form>
        )}
      </Card>
    </div>
  );
}
