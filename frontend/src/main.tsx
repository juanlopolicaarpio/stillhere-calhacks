import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { SpacetimeDBProvider } from 'spacetimedb/react'
import './index.css'
import App from './App.tsx'
import { spacetimeConnectionBuilder } from './spacetime_bindings/connection'

// A single SpacetimeDB connection attempt for the whole app. This never
// throws and never blocks rendering if SpacetimeDB is unreachable — see
// src/hooks/useSpacetime.ts for how that is reported honestly in the UI.
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <SpacetimeDBProvider connectionBuilder={spacetimeConnectionBuilder}>
      <App />
    </SpacetimeDBProvider>
  </StrictMode>,
)
