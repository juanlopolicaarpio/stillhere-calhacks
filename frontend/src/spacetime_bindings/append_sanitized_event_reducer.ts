// Hand-written SpacetimeDB client binding — see start_session_reducer.ts.
import { t } from 'spacetimedb';

export default {
  eventId: t.string(),
  seq: t.u64(),
  sessionId: t.option(t.string()),
  proposalId: t.option(t.string()),
  callId: t.option(t.string()),
  eventType: t.string(),
  safeSummary: t.string(),
  payloadJson: t.string(),
  simulated: t.bool(),
};
