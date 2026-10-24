import { useEffect, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import {
  ApiError,
  createConsent,
  getConsents,
  revokeConsent,
  type AllowedAction,
  type Consent,
} from '../api/client';
import { Button, Card, Field, Input, PageHeader } from '../components/ui';
import { ErrorNotice } from '../components/ErrorNotice';
import { useLocalStorage } from '../hooks/useLocalStorage';

const ACTIONS: AllowedAction[] = ['voice_call', 'sms'];

export function ConsentSettings() {
  const [userId] = useLocalStorage('stillhere_user_id');
  const [consents, setConsents] = useState<Consent[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [contactId, setContactId] = useState('');
  const [actions, setActions] = useState<AllowedAction[]>(['sms']);
  const [fields, setFields] = useState('first_name, shared_interests');
  const [quietStart, setQuietStart] = useState('21:00');
  const [quietEnd, setQuietEnd] = useState('09:00');
  const [maxPerDay, setMaxPerDay] = useState('1');
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const [revokingId, setRevokingId] = useState<string | null>(null);
  const [revokeError, setRevokeError] = useState<string | null>(null);

  async function loadConsents() {
    if (!userId) return;
    setLoading(true);
    setLoadError(null);
    try {
      const res = await getConsents(userId);
      setConsents(res);
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : 'Could not load consents.');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadConsents();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId]);

  function toggleAction(action: AllowedAction) {
    setActions((prev) =>
      prev.includes(action) ? prev.filter((a) => a !== action) : [...prev, action],
    );
  }

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    if (!userId) return;
    setSubmitting(true);
    setFormError(null);
    try {
      await createConsent({
        patient_id: userId,
        contact_id: contactId.trim(),
        allowed_actions: actions,
        allowed_fields: fields
          .split(',')
          .map((f) => f.trim())
          .filter(Boolean),
        quiet_hours_start: quietStart || undefined,
        quiet_hours_end: quietEnd || undefined,
        max_per_day: maxPerDay ? Number(maxPerDay) : undefined,
      });
      setContactId('');
      await loadConsents();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : 'Could not save this consent.');
    } finally {
      setSubmitting(false);
    }
  }

  async function handleRevoke(consentContactId: string) {
    if (!userId) return;
    setRevokingId(consentContactId);
    setRevokeError(null);
    try {
      await revokeConsent({ patient_id: userId, contact_id: consentContactId });
      await loadConsents();
    } catch (err) {
      setRevokeError(err instanceof ApiError ? err.message : 'Could not revoke this consent.');
    } finally {
      setRevokingId(null);
    }
  }

  if (!userId) {
    return (
      <div className="mx-auto max-w-lg">
        <PageHeader title="Consent settings" />
        <Card>
          <p className="text-sm" style={{ color: 'var(--sh-text-muted)' }}>
            We don't have a signed-up account in this browser yet.{' '}
            <Link to="/signup" className="font-semibold" style={{ color: 'var(--sh-accent)' }}>
              Sign up first
            </Link>
            .
          </p>
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-2xl space-y-8">
      <PageHeader
        title="Consent settings"
        subtitle="Exactly what each person in your circle has agreed to, and your control to change or revoke it at any time."
      />

      <Card>
        <h2 className="mb-4 text-lg font-semibold">Set or update consent for a contact</h2>
        <p className="mb-4 text-xs" style={{ color: 'var(--sh-text-muted)' }}>
          Contact ID comes from a contact who has accepted your invite (see My Circle, or the
          accept confirmation they trigger).
        </p>
        <form className="space-y-4" onSubmit={handleCreate}>
          <Field label="Contact ID">
            <Input required value={contactId} onChange={(e) => setContactId(e.target.value)} />
          </Field>

          <div>
            <span className="mb-1.5 block text-sm font-medium">Allowed actions</span>
            <div className="flex gap-3">
              {ACTIONS.map((action) => (
                <label
                  key={action}
                  className={`cursor-pointer rounded-xl border px-4 py-2 text-sm ${
                    actions.includes(action) ? 'border-[var(--sh-accent)] text-[var(--sh-accent)]' : ''
                  }`}
                  style={{ borderColor: actions.includes(action) ? undefined : 'var(--sh-border)' }}
                >
                  <input
                    type="checkbox"
                    className="sr-only"
                    checked={actions.includes(action)}
                    onChange={() => toggleAction(action)}
                  />
                  {action === 'voice_call' ? 'Voice calls' : 'Text messages'}
                </label>
              ))}
            </div>
          </div>

          <Field label="Allowed fields (comma separated)">
            <Input value={fields} onChange={(e) => setFields(e.target.value)} />
          </Field>

          <div className="grid grid-cols-3 gap-4">
            <Field label="Quiet hours start">
              <Input type="time" value={quietStart} onChange={(e) => setQuietStart(e.target.value)} />
            </Field>
            <Field label="Quiet hours end">
              <Input type="time" value={quietEnd} onChange={(e) => setQuietEnd(e.target.value)} />
            </Field>
            <Field label="Max contacts per day">
              <Input
                type="number"
                min={0}
                value={maxPerDay}
                onChange={(e) => setMaxPerDay(e.target.value)}
              />
            </Field>
          </div>

          <ErrorNotice message={formError} />

          <Button type="submit" variant="primary" disabled={submitting}>
            {submitting ? 'Saving…' : 'Save consent'}
          </Button>
        </form>
      </Card>

      <Card>
        <h2 className="mb-4 text-lg font-semibold">Current consents</h2>
        <ErrorNotice message={loadError} />
        {loading ? (
          <p className="text-sm" style={{ color: 'var(--sh-text-muted)' }}>
            Loading…
          </p>
        ) : consents.length === 0 ? (
          <p className="text-sm" style={{ color: 'var(--sh-text-muted)' }}>
            No consents on file yet.
          </p>
        ) : (
          <ul className="space-y-3">
            {consents.map((c) => (
              <li key={c.id} className="rounded-xl border p-4 text-sm" style={{ borderColor: 'var(--sh-border)' }}>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="font-medium">Contact {c.contact_id}</p>
                  <span
                    className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${
                      c.active
                        ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-200'
                        : 'bg-stone-200 text-stone-600 dark:bg-stone-500/15 dark:text-stone-300'
                    }`}
                  >
                    {c.active ? 'Active' : 'Inactive'}
                  </span>
                </div>
                <p className="mt-2" style={{ color: 'var(--sh-text-muted)' }}>
                  Allowed: {c.allowed_actions.join(', ') || 'none'} · Fields:{' '}
                  {c.allowed_fields.join(', ') || 'none'}
                </p>
                <p style={{ color: 'var(--sh-text-muted)' }}>
                  Quiet hours: {c.quiet_hours_start ?? '—'} – {c.quiet_hours_end ?? '—'} · Max/day:{' '}
                  {c.max_per_day ?? '—'}
                </p>
                <div className="mt-3">
                  <Button
                    type="button"
                    variant="danger"
                    disabled={revokingId === c.contact_id}
                    onClick={() => handleRevoke(c.contact_id)}
                  >
                    {revokingId === c.contact_id ? 'Revoking…' : 'Revoke'}
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
        <ErrorNotice message={revokeError} />
      </Card>
    </div>
  );
}
