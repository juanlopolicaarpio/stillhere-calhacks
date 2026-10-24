// Hand-written SpacetimeDB client binding — see start_session_reducer.ts.
import { t } from 'spacetimedb';

export default {
  sessionId: t.string(),
  reason: t.string(),
};
