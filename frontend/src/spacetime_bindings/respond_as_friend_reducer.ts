// Hand-written SpacetimeDB client binding — see start_session_reducer.ts.
import { t } from 'spacetimedb';

export default {
  responseId: t.string(),
  proposalId: t.string(),
  version: t.u32(),
  response: t.string(),
  agreedActivity: t.string(),
  agreedStartsAt: t.string(),
  simulated: t.bool(),
};
