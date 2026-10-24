// Hand-written SpacetimeDB client binding — see start_session_reducer.ts.
import { t } from 'spacetimedb';

export default {
  callId: t.string(),
  sessionId: t.string(),
  providerCallId: t.option(t.string()),
  toNumberRedacted: t.string(),
  status: t.string(),
  mode: t.string(),
  simulated: t.bool(),
  error: t.option(t.string()),
};
