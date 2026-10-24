import type {
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  LabelHTMLAttributes,
  ReactNode,
  TextareaHTMLAttributes,
} from 'react';

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={`rounded-2xl border p-6 shadow-sm ${className}`}
      style={{ borderColor: 'var(--sh-border)', background: 'var(--sh-surface)' }}
    >
      {children}
    </div>
  );
}

export function Button({
  variant = 'primary',
  className = '',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'danger' }) {
  const base = 'rounded-full px-5 py-2.5 text-sm font-semibold transition-opacity disabled:cursor-not-allowed disabled:opacity-50';
  const variants: Record<string, string> = {
    primary: 'text-white hover:opacity-90',
    secondary: 'border hover:bg-[var(--sh-accent-soft)]',
    danger: 'border border-red-300 text-red-700 hover:bg-red-50 dark:border-red-400/40 dark:text-red-300 dark:hover:bg-red-500/10',
  };
  const style =
    variant === 'primary'
      ? { background: 'var(--sh-accent)' }
      : variant === 'secondary'
        ? { borderColor: 'var(--sh-border)', color: 'var(--sh-text)' }
        : undefined;
  return (
    <button className={`${base} ${variants[variant]} ${className}`} style={style} {...props} />
  );
}

export function Label({ children, ...props }: LabelHTMLAttributes<HTMLLabelElement>) {
  return (
    <label className="mb-1.5 block text-sm font-medium" {...props}>
      {children}
    </label>
  );
}

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className="w-full rounded-xl border bg-transparent px-4 py-2.5 text-sm outline-none focus:ring-2"
      style={{ borderColor: 'var(--sh-border)' }}
      {...props}
    />
  );
}

export function Textarea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className="w-full rounded-xl border bg-transparent px-4 py-2.5 text-sm outline-none focus:ring-2"
      style={{ borderColor: 'var(--sh-border)' }}
      {...props}
    />
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <Label>{label}</Label>
      {children}
    </div>
  );
}

export function PageHeader({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div className="mb-8">
      <h1 className="text-3xl font-semibold tracking-tight" style={{ color: 'var(--sh-text)' }}>
        {title}
      </h1>
      {subtitle && (
        <p className="mt-2 text-base" style={{ color: 'var(--sh-text-muted)' }}>
          {subtitle}
        </p>
      )}
    </div>
  );
}
