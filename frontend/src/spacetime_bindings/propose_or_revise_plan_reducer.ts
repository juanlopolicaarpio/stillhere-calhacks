// Hand-written SpacetimeDB client binding — see start_session_reducer.ts.
import { t } from 'spacetimedb';

export default {
  proposalId: t.string(),
  sessionId: t.string(),
  version: t.u32(),
  activity: t.string(),
  location: t.option(t.string()),
  startsAt: t.string(),
  whyContact: t.string(),
  whyActivity: t.string(),
  memoryCitations: t.array(t.string()),
  supersedesVersion: t.option(t.u32()),
};
