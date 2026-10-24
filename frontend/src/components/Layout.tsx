import { NavLink, Outlet } from 'react-router-dom';
import { StatusBanner } from './StatusBanner';

const navItems = [
  { to: '/', label: 'Home' },
  { to: '/signup', label: 'Sign up' },
  { to: '/onboarding', label: 'Onboarding' },
  { to: '/circle', label: 'My circle' },
  { to: '/settings/consent', label: 'Consent settings' },
  { to: '/companion', label: 'Companion' },
  { to: '/dashboard', label: 'Judge dashboard' },
  { to: '/sync-proof', label: 'Sync proof' },
];

export function Layout() {
  return (
    <div className="flex min-h-screen flex-col" style={{ background: 'var(--sh-bg)', color: 'var(--sh-text)' }}>
      <StatusBanner />
      <header className="border-b" style={{ borderColor: 'var(--sh-border)' }}>
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-6 py-4">
          <NavLink to="/" className="text-lg font-semibold tracking-tight">
            Still&nbsp;Here
          </NavLink>
          <nav className="flex flex-wrap gap-4 text-sm">
            {navItems.slice(1).map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  `rounded-full px-3 py-1 transition-colors ${
                    isActive
                      ? 'bg-[var(--sh-accent-soft)] text-[var(--sh-accent)]'
                      : 'text-[var(--sh-text-muted)] hover:text-[var(--sh-text)]'
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>
      <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-10">
        <Outlet />
      </main>
      <footer className="border-t px-6 py-6 text-center text-xs text-[var(--sh-text-muted)]" style={{ borderColor: 'var(--sh-border)' }}>
        Still Here is a voluntary companion tool. It is not a medical device and does not
        provide diagnosis, treatment, or emergency services.
      </footer>
    </div>
  );
}
