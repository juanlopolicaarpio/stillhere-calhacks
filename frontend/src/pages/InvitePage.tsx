import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import {
  ApiError,
  acceptInvite,
  declineInvite,
  getInvite,
  type InviteDetails,
} from '../api/client';
import { Button, Card, PageHeader } from '../components/ui';
import { ErrorNotice } from '../components/ErrorNotice';

type Outcome = 'accepted' | 'declined' | null;

export function InvitePage() {
  const { token } = useParams<{ token: string }>();
  const [invite, setInvite] = useState<InviteDetails | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [outcome, setOutcome] = useState<Outcome>(null);

  useEffect(() => {
    if (!token) return;
    setLoading(true);
    getInvite(token)
      .then((details) => {
        setInvite(details);
        if (details.status === 'accepted') setOutcome('accepted');
        if (details.status === 'declined') setOutcome('declined');
      })
      .catch((err) =>
        setLoadError(err instanceof ApiError ? err.message : 'Could not load this invite.'),
      )
      .finally(() => setLoading(false));
  }, [token]);

  async function handleAccept() {
    if (!token) return;
    setSubmitting(true);
    setActionError(null);
    try {
      await acceptInvite(token);
      setOutcome('accepted');
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : 'Could not accept the invite.');
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDecline() {
    if (!token) return;
    setSubmitting(true);
    setActionError(null);
    try {
      await declineInvite(token);
      setOutcome('declined');
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : 'Could not decline the invite.');
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <div className="mx-auto max-w-lg">
        <Card>
          <p className="text-sm" style={{ color: 'var(--sh-text-muted)' }}>
            Loading invite…
          </p>
        </Card>
      </div>
    );
  }

  if (loadError || !invite) {
    return (
      <div className="mx-auto max-w-lg">
        <PageHeader title="Invite not found" />
        <Card>
          <ErrorNotice message={loadError ?? 'This invite link is not valid.'} />
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-lg">
      <PageHeader title="You've been invited to stay in touch" />
      <Card>
        <p className="text-base leading-relaxed">
          <strong>{invite.patient_display_name}</strong> would like to add you,{' '}
          <strong>{invite.contact_name}</strong>, to their Still Here circle as their{' '}
          <strong>{invite.relationship_label}</strong>.
        </p>

        <div
          className="mt-5 rounded-xl border p-4 text-sm leading-relaxed"
          style={{ borderColor: 'var(--sh-border)', color: 'var(--sh-text-muted)' }}
        >
          Accepting means you opt in to receiving occasional AI-assisted calls or text messages
          from Still Here on {invite.patient_display_name}'s behalf, to help coordinate getting
          together. You are always free to reply <strong>STOP</strong> to any message, or revoke
          this at any time — nothing is permanent.
        </div>

        {outcome === 'accepted' ? (
          <p className="mt-6 font-medium" style={{ color: 'var(--sh-accent)' }}>
            You're in. Thank you — {invite.patient_display_name} will be notified.
          </p>
        ) : outcome === 'declined' ? (
          <p className="mt-6 font-medium" style={{ color: 'var(--sh-text-muted)' }}>
            You've declined this invite. No one will contact you about this again.
          </p>
        ) : (
          <>
            <ErrorNotice message={actionError} />
            <div className="mt-6 flex gap-3">
              <Button variant="primary" disabled={submitting} onClick={handleAccept}>
                Accept
              </Button>
              <Button variant="secondary" disabled={submitting} onClick={handleDecline}>
                Decline
              </Button>
            </div>
          </>
        )}

        <p className="mt-6 text-xs" style={{ color: 'var(--sh-text-muted)' }}>
          This invite expires {new Date(invite.expires_at).toLocaleString()}.
        </p>
      </Card>
    </div>
  );
}
