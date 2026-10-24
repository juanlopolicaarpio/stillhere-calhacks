// Hand-written SpacetimeDB client binding — see connection_sessions_table.ts
// for why. Transcribed from spacetime/spacetimedb/src/schema.ts.
import { t } from 'spacetimedb';

export default t.row({
  consentId: t.string().primaryKey(),
  sessionId: t.string().index('btree'),
  patientOutreachActive: t.bool(),
  contactOptedIn: t.bool(),
  contactPhoneVerified: t.bool(),
  allowedActions: t.array(t.string()),
  revoked: t.bool(),
  reason: t.string(),
  updatedAt: t.timestamp(),
});
