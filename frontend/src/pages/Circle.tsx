import { useEffect, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import {
  ApiError,
  activateOutreach,
  createCircleInvite,
  type CircleInviteResponse,
} from '../api/client';
import { Button, Card, Field, Input, PageHeader } from '../components/ui';
import { ErrorNotice } from '../components/ErrorNotice';
import { useLocalStorage } from '../hooks/useLocalStorage';

interface StoredInvite extends CircleInviteResponse {
  contact_name: string;
  relationship_label: string;
}

export function Circle() {
  const [userId] = useLocalStorage('stillhere_user_id');
  const [name, setName] = useState('');
  const [phone, setPhone] = useState('');
  const [relationship, setRelationship] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [invites, setInvites] = useState<StoredInvite[]>([]);
  const [copiedToken, setCopiedToken] = useState<string | null>(null);

  const [outreachActive, setOutreachActive] = useState<boolean | null>(null);
  const [outreachSubmitting, setOutreachSubmitting] = useState(false);
  const [outreachError, setOutreachError] = useState<string | null>(null);

  const storageKey = userId ? `stillhere_invites_${userId}` : null;

  useEffect(() => {
    if (!storageKey) return;
    try {
      const raw = window.localStorage.getItem(storageKey);
      if (raw) setInvites(JSON.parse(raw) as StoredInvite[]);
    } catch {
      // ignore
    }
  }, [storageKey]);

  function persistInvites(next: StoredInvite[]) {
    setInvites(next);
    if (storageKey) {
      try {
        window.localStorage.setItem(storageKey, JSON.stringify(next));
      } catch {
        // ignore
      }
    }
  }

  async function handleAddContact(e: FormEvent) {
    e.preventDefault();
    if (!userId) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await createCircleInvite({
        patient_id: userId,
        contact_name: name.trim(),
        contact_phone_e164: phone.trim(),
        relationship_label: relationship.trim(),
      });
      persistInvites([
        { ...res, contact_name: name.trim(), relationship_label: relationship.trim() },
        ...invites,
      ]);
      setName('');
      setPhone('');
      setRelationship('');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create the invite.');
    } finally {
      setSubmitting(false);
    }
  }

  async function handleActivate(active: boolean) {
    if (!userId) return;
    setOutreachSubmitting(true);
    setOutreachError(null);
    try {
      const res = await activateOutreach({ user_id: userId, active });
      setOutreachActive(res.outreach_active);
    } catch (err) {
      setOutreachError(err instanceof ApiError ? err.message : 'Could not update outreach.');
    } finally {
      setOutreachSubmitting(false);
    }
  }

  async function copyInviteUrl(token: string, url: string) {
    try {
      await navigator.clipboard.writeText(url);
      setCopiedToken(token);
      setTimeout(() => setCopiedToken((cur) => (cur === token ? null : cur)), 2000);
    } catch {
      setCopiedToken(null);
    }
  }

  if (!userId) {
    return (
      <div className="mx-auto max-w-lg">
        <PageHeader title="Your circle" />
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
        title="Your circle"
        subtitle="Add the people you'd like to stay in touch with. Nothing is contacted until they accept their own invite."
      />

      <Card>
        <h2 className="mb-4 text-lg font-semibold">Add someone</h2>
        <form className="space-y-4" onSubmit={handleAddContact}>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Their name">
              <Input required value={name} onChange={(e) => setName(e.target.value)} placeholder="Josh" />
            </Field>
            <Field label="Relationship to you">
              <Input
                required
                value={relationship}
                onChange={(e) => setRelationship(e.target.value)}
                placeholder="Best friend"
              />
            </Field>
          </div>
          <Field label="Their phone number (E.164)">
            <Input
              required
              type="tel"
              pattern="^\+[1-9]\d{6,14}$"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              placeholder="+14155550123"
            />
          </Field>
          <ErrorNotice message={error} />
          <Button type="submit" variant="primary" disabled={submitting}>
            {submitting ? 'Creating invite…' : 'Create invite'}
          </Button>
        </form>
      </Card>

      <Card>
        <h2 className="mb-4 text-lg font-semibold">Pending and sent invites</h2>
        {invites.length === 0 ? (
          <p className="text-sm" style={{ color: 'var(--sh-text-muted)' }}>
            No invites yet. Add someone above to get started.
          </p>
        ) : (
          <ul className="space-y-3">
            {invites.map((invite) => (
              <li
                key={invite.token}
                className="rounded-xl border p-4 text-sm"
                style={{ borderColor: 'var(--sh-border)' }}
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <p className="font-medium">{invite.contact_name}</p>
                    <p style={{ color: 'var(--sh-text-muted)' }}>{invite.relationship_label}</p>
                  </div>
                  <p className="text-xs" style={{ color: 'var(--sh-text-muted)' }}>
                    Expires {new Date(invite.expires_at).toLocaleString()}
                  </p>
                </div>
                <div className="mt-3 flex items-center gap-2">
                  <code
                    className="flex-1 truncate rounded-lg px-3 py-2 text-xs"
                    style={{ background: 'var(--sh-accent-soft)' }}
                  >
                    {invite.invite_url}
                  </code>
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={() => copyInviteUrl(invite.token, invite.invite_url)}
                  >
                    {copiedToken === invite.token ? 'Copied' : 'Copy'}
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card>
        <h2 className="mb-2 text-lg font-semibold">Activate outreach</h2>
        <p className="mb-4 text-sm" style={{ color: 'var(--sh-text-muted)' }}>
          Turning this on does not immediately contact anyone. Outreach to any individual friend
          only happens after that friend separately opens their invite link and accepts — and
          only within the limits you set in Consent Settings.
        </p>
        <div className="flex items-center gap-3">
          <Button
            type="button"
            variant="primary"
            disabled={outreachSubmitting}
            onClick={() => handleActivate(true)}
          >
            {outreachSubmitting ? 'Updating…' : 'Activate outreach'}
          </Button>
          <Button
            type="button"
            variant="secondary"
            disabled={outreachSubmitting}
            onClick={() => handleActivate(false)}
          >
            Pause outreach
          </Button>
          {outreachActive !== null && (
            <span className="text-sm" style={{ color: 'var(--sh-text-muted)' }}>
              Currently: <strong>{outreachActive ? 'active' : 'paused'}</strong>
            </span>
          )}
        </div>
        <ErrorNotice message={outreachError} />
      </Card>
    </div>
  );
}
