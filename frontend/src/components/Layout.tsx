import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { StatusBanner } from './StatusBanner';

const navItems = [
  { to: '/', label: 'Home' },
  { to: '/signup', label: 'Sign up' },
  { to: '/onboarding', label: 'Onboarding' },
  { to: '/circle', label: 'My circle' },
  { to: '/settings/consent', label: 'Consent settings' },
  { to: '/companion', label: 'Companion' },
  { to: '/dashboard', label: 'Judge dashboard' },
  { to: '/pitch-demo', label: 'Live coordination' },
  { to: '/sync-proof', label: 'Sync proof' },
];

export function Layout() {
  const location = useLocation();
  const demoMode = location.pathname === '/pitch-demo' || location.pathname === '/call-now';
  const demoTheme = demoMode ? {
    '--sh-bg': '#f1dfc4',
    '--sh-surface': '#fffdf8',
    '--sh-border': '#d8b98d',
    '--sh-accent': '#9a5b32',
    '--sh-accent-soft': '#f5e7d3',
    '--sh-text': '#472e1b',
    '--sh-text-muted': '#7c6044',
  } as React.CSSProperties : undefined;
  return (
    <div className="flex min-h-screen flex-col" style={{ ...demoTheme, background: 'var(--sh-bg)', color: 'var(--sh-text)' }}>
      {!demoMode && <StatusBanner />}
      <header className="border-b bg-[var(--sh-surface)]/90 backdrop-blur" style={{ borderColor: 'var(--sh-border)' }}>
        <div className={`mx-auto flex flex-wrap items-center justify-between gap-3 px-6 py-4 ${demoMode ? 'max-w-7xl' : 'max-w-6xl'}`}>
          <NavLink to={demoMode ? '/pitch-demo' : '/'} className="flex items-center gap-3 tracking-tight">
            <span className="relative flex h-11 w-11 items-center justify-center rounded-2xl bg-[var(--sh-accent)] shadow-sm" aria-hidden="true">
              <span className="absolute left-2.5 top-2.5 h-4 w-4 rounded-full border-2 border-white" />
              <span className="absolute bottom-2.5 right-2.5 h-4 w-4 rounded-full border-2 border-white" />
              <span className="h-0.5 w-4 rotate-[-38deg] bg-white" />
            </span>
            <span>
              <span className="block text-lg font-semibold leading-none">Still Here</span>
              {demoMode && <span className="mt-1 block text-[11px] font-medium uppercase tracking-[0.16em] text-[var(--sh-text-muted)]">Closer, one conversation at a time</span>}
            </span>
          </NavLink>
          {!demoMode && <nav className="flex flex-wrap gap-4 text-sm">
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
          </nav>}
        </div>
      </header>
      <main className={`mx-auto w-full flex-1 px-5 ${demoMode ? 'max-w-7xl py-7 sm:px-8' : 'max-w-6xl py-10'}`}>
        <Outlet />
      </main>
      <footer className="border-t bg-[var(--sh-surface)]/70 px-6 py-6 text-center text-xs text-[var(--sh-text-muted)]" style={{ borderColor: 'var(--sh-border)' }}>
        Still Here is a voluntary companion tool. It is not a medical device and does not
        provide diagnosis, treatment, or emergency services.
      </footer>
    </div>
  );
}
