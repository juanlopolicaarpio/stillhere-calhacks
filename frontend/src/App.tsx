import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { Layout } from './components/Layout';
import { Landing } from './pages/Landing';
import { Signup } from './pages/Signup';
import { Onboarding } from './pages/Onboarding';
import { Circle } from './pages/Circle';
import { InvitePage } from './pages/InvitePage';
import { ConsentSettings } from './pages/ConsentSettings';
import { Companion } from './pages/Companion';
import { Dashboard } from './pages/Dashboard';
import { SyncProof } from './pages/SyncProof';
import { JudgeHub } from './pages/JudgeHub';
import { CallNow } from './pages/CallNow';

function NotFound() {
  return (
    <div className="mx-auto max-w-lg text-center">
      <h1 className="text-2xl font-semibold">Page not found</h1>
      <p className="mt-2 text-sm" style={{ color: 'var(--sh-text-muted)' }}>
        <a href="/" style={{ color: 'var(--sh-accent)' }}>
          Go back home
        </a>
      </p>
    </div>
  );
}

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<Landing />} />
          <Route path="/signup" element={<Signup />} />
          <Route path="/onboarding" element={<Onboarding />} />
          <Route path="/circle" element={<Circle />} />
          <Route path="/invite/:token" element={<InvitePage />} />
          <Route path="/settings/consent" element={<ConsentSettings />} />
          <Route path="/companion" element={<Companion />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/sync-proof" element={<SyncProof />} />
          <Route path="/pitch-demo" element={<JudgeHub />} />
          <Route path="/call-now" element={<CallNow />} />
          <Route path="/404" element={<NotFound />} />
          <Route path="*" element={<Navigate to="/404" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

export default App;
