import { useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { ApiError, signup, verify, type PreferredChannel } from '../api/client';
import { Button, Card, Field, Input, PageHeader } from '../components/ui';
import { ErrorNotice } from '../components/ErrorNotice';
import { useLocalStorage } from '../hooks/useLocalStorage';

type Step = 'form' | 'verify';

export function Signup() {
  const navigate = useNavigate();
  const [, setUserId] = useLocalStorage('stillhere_user_id');
  const [, setDisplayName] = useLocalStorage('stillhere_display_name');

  const [step, setStep] = useState<Step>('form');
  const [phone, setPhone] = useState('');
  const [displayNameInput, setDisplayNameInput] = useState('');
  const [channel, setChannel] = useState<PreferredChannel>('text');
  const [attested, setAttested] = useState(false);
  const [code, setCode] = useState('');
  const [devCode, setDevCode] = useState<string | null>(null);
  const [pendingUserId, setPendingUserId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSignup(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!attested) {
      setError('You must confirm you are 18 or older to continue.');
      return;
    }
    setSubmitting(true);
    try {
      const res = await signup({
        phone_e164: phone.trim(),
        preferred_channel: channel,
        adult_attested: attested,
        display_name: displayNameInput.trim(),
      });
      setPendingUserId(res.user_id);
      setDevCode(res.dev_code ?? null);
      setStep('verify');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong. Please try again.');
    } finally {
      setSubmitting(false);
    }
  }

  async function handleVerify(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const res = await verify({ phone_e164: phone.trim(), code: code.trim() });
      setUserId(res.user_id);
      setDisplayName(displayNameInput.trim());
      navigate('/onboarding');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Verification failed. Please try again.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="mx-auto max-w-lg">
      <PageHeader
        title="Let's get you set up"
        subtitle="This takes about a minute. You can change any of this later."
      />

      <Card>
        {step === 'form' ? (
          <form className="space-y-5" onSubmit={handleSignup}>
            <Field label="Your name">
              <Input
                required
                placeholder="What should we call you?"
                value={displayNameInput}
                onChange={(e) => setDisplayNameInput(e.target.value)}
              />
            </Field>

            <Field label="Phone number (E.164 format, e.g. +14155550123)">
              <Input
                required
                type="tel"
                placeholder="+14155550123"
                pattern="^\+[1-9]\d{6,14}$"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
              />
            </Field>

            <div>
              <span className="mb-1.5 block text-sm font-medium">How should we reach you to verify?</span>
              <div className="flex gap-3">
                {(['text', 'call'] as PreferredChannel[]).map((option) => (
                  <label
                    key={option}
                    className={`flex-1 cursor-pointer rounded-xl border px-4 py-2.5 text-center text-sm font-medium ${
                      channel === option ? 'border-[var(--sh-accent)] text-[var(--sh-accent)]' : ''
                    }`}
                    style={{ borderColor: channel === option ? undefined : 'var(--sh-border)' }}
                  >
                    <input
                      type="radio"
                      name="channel"
                      value={option}
                      checked={channel === option}
                      onChange={() => setChannel(option)}
                      className="sr-only"
                    />
                    {option === 'text' ? 'Text me' : 'Call me'}
                  </label>
                ))}
              </div>
            </div>

            <label className="flex items-start gap-3 text-sm">
              <input
                type="checkbox"
                className="mt-1"
                checked={attested}
                onChange={(e) => setAttested(e.target.checked)}
              />
              <span>
                I confirm I am <strong>18 years of age or older</strong>, and I am choosing to use
                Still Here voluntarily. No diagnosis, prescription, or clinical referral is
                required.
              </span>
            </label>

            <p className="text-xs leading-relaxed" style={{ color: 'var(--sh-text-muted)' }}>
              By continuing, you consent to Still Here contacting you at this number to verify
              your identity and to deliver the companion service you request. You can stop at any
              time by replying STOP, or from Consent Settings.
            </p>

            <ErrorNotice message={error} />

            <Button type="submit" variant="primary" disabled={submitting} className="w-full">
              {submitting ? 'Sending…' : 'Continue'}
            </Button>
          </form>
        ) : (
          <form className="space-y-5" onSubmit={handleVerify}>
            <p className="text-sm" style={{ color: 'var(--sh-text-muted)' }}>
              We sent a verification code to <strong>{phone}</strong>. Enter it below.
            </p>

            {devCode && (
              <div
                className="rounded-xl border border-dashed p-4 text-sm"
                style={{ borderColor: 'var(--sh-accent)', background: 'var(--sh-accent-soft)' }}
              >
                <p className="font-semibold uppercase tracking-wide text-xs mb-1">
                  Local dev only
                </p>
                <p>
                  No SMS provider is configured, so here is the code directly:{' '}
                  <code className="font-mono text-base">{devCode}</code>
                </p>
              </div>
            )}

            <Field label="Verification code">
              <Input
                required
                inputMode="numeric"
                placeholder="123456"
                value={code}
                onChange={(e) => setCode(e.target.value)}
              />
            </Field>

            <ErrorNotice message={error} />

            <div className="flex gap-3">
              <Button
                type="button"
                variant="secondary"
                onClick={() => {
                  setStep('form');
                  setError(null);
                }}
              >
                Back
              </Button>
              <Button type="submit" variant="primary" disabled={submitting} className="flex-1">
                {submitting ? 'Verifying…' : 'Verify and continue'}
              </Button>
            </div>

            {pendingUserId && (
              <p className="text-xs" style={{ color: 'var(--sh-text-muted)' }}>
                User id: <code>{pendingUserId}</code>
              </p>
            )}
          </form>
        )}
      </Card>
    </div>
  );
}
