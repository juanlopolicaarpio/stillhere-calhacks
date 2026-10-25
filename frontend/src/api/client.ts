// Single typed API client mirroring specs/CONTRACTS.md exactly.
// Do not invent endpoints or rename fields — this file is the frozen contract surface
// for the frontend. If something here looks wrong, it should be reported to the lead,
// not quietly changed.

// ---------------------------------------------------------------------------
// Shared primitives
// ---------------------------------------------------------------------------

export type ErrorCode =
  | 'validation_error'
  | 'consent_denied'
  | 'not_found'
  | 'conflict'
  | 'illegal_transition'
  | 'provider_error'
  | 'not_configured'
  | 'unauthorized';

export interface ApiErrorEnvelope {
  error: {
    code: ErrorCode | string;
    message: string;
  };
}

export class ApiError extends Error {
  code: ErrorCode | string;
  status: number;

  constructor(code: ErrorCode | string, message: string, status: number) {
    super(message);
    this.code = code;
    this.status = status;
    this.name = 'ApiError';
  }
}

export type VoiceMode = 'cartesia_pstn' | 'simulated';

export type PreferredChannel = 'call' | 'text';

export type VisibilityLevel =
  | 'private_to_sam'
  | 'agent_internal'
  | 'shareable_with_contact'
  | 'public_demo_safe';

export type MemoryKind = 'fact' | 'preference' | 'relationship' | 'boundary';

export type ReasoningMode = 'claude' | 'fallback';

export type RelationshipStatus = 'accepted' | 'declined';

export type PlanStatus =
  | 'OPPORTUNITY_IDENTIFIED'
  | 'CONSENT_VERIFIED'
  | 'MEMORY_RETRIEVED'
  | 'CANDIDATE_PLAN_CREATED'
  | 'CONTACT_IN_PROGRESS'
  | 'FRIEND_ACCEPTED'
  | 'FRIEND_DECLINED'
  | 'FRIEND_COUNTERPROPOSED'
  | 'PATIENT_APPROVAL_PENDING'
  | 'PATIENT_ACCEPTED'
  | 'PATIENT_DECLINED'
  | 'AVAILABILITY_REVALIDATED'
  | 'PLAN_CONFIRMED'
  | 'NOTIFICATIONS_SENT'
  | 'COMPLETE'
  | 'HALTED_CONSENT_REVOKED';

export type EventType =
  | 'demo.reset'
  | 'demo.start'
  | 'consent.verified'
  | 'consent.denied'
  | 'consent.revoked'
  | 'memory.retrieved'
  | 'memory.extracted'
  | 'memory.corrected'
  | 'memory.deleted'
  | 'plan.candidate_created'
  | 'plan.state_changed'
  | 'plan.confirmed'
  | 'plan.rejected'
  | 'call.requested'
  | 'call.accepted_by_provider'
  | 'call.ringing'
  | 'call.answered'
  | 'call.first_audio'
  | 'call.turn'
  | 'call.tool_invoked'
  | 'call.ended'
  | 'call.failed'
  | 'friend.responded'
  | 'patient.approval_requested'
  | 'patient.responded'
  | 'notification.sent'
  | 'safety.blocked'
  | 'error'
  // specs/CONTRACTS_V2.md §6 additions.
  | 'stdb.reducer_called'
  | 'stdb.reducer_rejected'
  | 'stdb.unavailable'
  | 'conversation.interpreted'
  | 'conversation.clarification_requested'
  | 'proposal.revised'
  | 'approval.invalidated';

/** specs/CONTRACTS_V2.md §5 — stable intent vocabulary for conversation turns. */
export type ConversationIntent =
  | 'accept'
  | 'decline'
  | 'maybe_defer'
  | 'counter_time'
  | 'counter_activity'
  | 'counter_both'
  | 'ask_why_activity'
  | 'ask_about_patient_health'
  | 'ask_what_patient_likes'
  | 'ask_add_person'
  | 'request_stop_contact'
  | 'interrupt'
  | 'silence'
  | 'out_of_scope'
  | 'clarification_needed'
  | 'unknown';

// ---------------------------------------------------------------------------
// 4.1 Public / onboarding
// ---------------------------------------------------------------------------

export interface HealthResponse {
  status: string;
  db: string;
  voice_mode: VoiceMode;
}

/** specs/CONTRACTS_V2.md §4 degradation policy: 'spacetimedb' only when the
 * backend's adapter is configured AND reachable; 'sqlite_fallback' otherwise. */
export type CoordinationMode = 'spacetimedb' | 'sqlite_fallback';

/** specs/CONTRACTS_V2.md §5: 'claude' unless ANTHROPIC_API_KEY is absent. */
export type ConversationMode = 'claude' | 'keyword_fallback';

/** specs/CONTRACTS_V3.md §3: real calendar provider, or the seeded fallback. */
export type CalendarMode = 'google' | 'microsoft' | 'seeded' | string;

/** specs/CONTRACTS_V3.md §5: a genuinely configured provider, or honest 'unbooked'. */
export type BookingMode = 'unbooked' | string;

export interface StatusResponse {
  voice_mode: VoiceMode;
  cartesia_configured?: boolean;
  cartesia_call_ready?: boolean;
  cartesia_version?: string;
  cartesia_agent_id_present?: boolean;
  cartesia_from_number_id_present?: boolean;
  anthropic_configured?: boolean;
  anthropic_model?: string;
  twilio_configured?: boolean;
  target_phone_present?: boolean;
  real_call_possible?: boolean;
  dev_dashboard_token?: string | null;
  notes?: string;
  // CONTRACTS_V2.md additions. Agent B/C are wiring these in parallel, so
  // they must be treated as optional and absent-tolerant everywhere.
  coordination_mode?: CoordinationMode;
  spacetimedb_reachable?: boolean;
  conversation_mode?: ConversationMode;
  // CONTRACTS_V3.md additions (agents H and J, built in parallel with this
  // frontend work) — optional and absent-tolerant until those land.
  calendar_mode?: CalendarMode;
  booking_mode?: BookingMode;
  [key: string]: unknown;
}

export interface SignupRequest {
  phone_e164: string;
  preferred_channel: PreferredChannel;
  adult_attested: boolean;
  display_name: string;
}

export interface SignupResponse {
  user_id: string;
  verification_required: true;
  dev_code?: string;
}

export interface VerifyRequest {
  phone_e164: string;
  code: string;
}

export interface VerifyResponse {
  user_id: string;
  verified: true;
}

export interface OnboardingPreferencesRequest {
  user_id: string;
  timezone?: string;
  activity_preferences?: string[];
  quiet_hours_start?: string;
  quiet_hours_end?: string;
}

export interface OkResponse {
  ok: true;
}

export interface OutreachActivateRequest {
  user_id: string;
  active: boolean;
}

export interface OutreachActivateResponse {
  user_id: string;
  outreach_active: boolean;
}

// ---------------------------------------------------------------------------
// 4.2 Circle & dual consent
// ---------------------------------------------------------------------------

export interface CircleInviteRequest {
  patient_id: string;
  contact_name: string;
  contact_phone_e164: string;
  relationship_label: string;
}

export interface CircleInviteResponse {
  token: string;
  invite_url: string;
  expires_at: string;
}

export type InviteStatus = 'pending' | 'accepted' | 'declined' | 'expired' | string;

export interface InviteDetails {
  patient_display_name: string;
  contact_name: string;
  relationship_label: string;
  expires_at: string;
  status: InviteStatus;
}

export interface InviteAcceptRequest {
  code?: string;
}

export interface InviteAcceptResponse {
  contact_id: string;
  relationship_status: 'accepted';
  consent_id: string;
}

export interface InviteDeclineResponse {
  relationship_status: 'declined';
}

export type AllowedAction = 'voice_call' | 'sms';

export interface Consent {
  id: string;
  contact_id: string;
  allowed_actions: AllowedAction[];
  allowed_fields: string[];
  quiet_hours_start: string | null;
  quiet_hours_end: string | null;
  max_per_day: number | null;
  active: boolean;
}

export interface CreateConsentRequest {
  patient_id: string;
  contact_id: string;
  allowed_actions: AllowedAction[];
  allowed_fields: string[];
  quiet_hours_start?: string;
  quiet_hours_end?: string;
  max_per_day?: number;
}

export interface CreateConsentResponse {
  consent_id: string;
}

export interface RevokeConsentRequest {
  patient_id: string;
  contact_id: string;
}

export interface RevokeConsentResponse {
  revoked: number;
}

export interface SmsStopRequest {
  phone_e164: string;
}

export interface SmsStopResponse {
  ok: boolean;
  consents_revoked: number;
}

// ---------------------------------------------------------------------------
// 4.3 Companion & memory
// ---------------------------------------------------------------------------

export interface CompanionMessageRequest {
  user_id: string;
  text: string;
  session_id?: string;
}

export interface CompanionMessageResponse {
  reply: string;
  session_id: string;
  memories_considered: Memory[];
  memories_extracted: Memory[];
  reasoning_mode: ReasoningMode;
}

export interface Memory {
  id: string;
  owner_id: string;
  subject_id: string;
  kind: MemoryKind;
  value: string;
  source_conversation_id: string | null;
  source_quote: string | null;
  confidence: number;
  confirmed: boolean;
  visibility: VisibilityLevel;
  created_at: string;
  updated_at: string;
}

export type MemoryAudience = 'owner' | 'planning' | 'contact' | 'demo';

export interface PatchMemoryRequest {
  value?: string;
  visibility?: VisibilityLevel;
}

export interface DeleteMemoryResponse {
  deleted: true;
}

// ---------------------------------------------------------------------------
// 4.4 Plans, demo, events
// ---------------------------------------------------------------------------

export interface DemoResetResponse {
  ok: boolean;
  reset_version: number;
  demo_session_id: string;
}

export interface DemoStartRequest {
  force_mode?: VoiceMode;
}

export interface DemoStartResponse {
  plan_id: string;
  call_id: string;
  mode: VoiceMode;
  state: PlanStatus;
}

export interface DemoSamResponseRequest {
  plan_id: string;
  response: 'accepted' | 'declined';
  activity?: string;
  starts_at?: string;
}

export interface DemoSamResponseResponse {
  plan_id: string;
  state: PlanStatus;
  simulated: true;
}

export interface Plan {
  id: string;
  patient_id: string;
  contact_id: string;
  activity: string;
  location: string | null;
  starts_at: string | null;
  status: PlanStatus;
  version: number;
  rationale: string | null;
  memory_citations: string[];
  confirmed_at: string | null;
}

/**
 * specs/CONTRACTS_V3.md §4 (lead-owned group planning): `plan_participants`
 * may contain MORE than one `role: 'contact'` row for the same plan. This
 * row never carries a contact's name or phone — only an internal user id —
 * so rendering the full list here does not disclose one contact's identity
 * to another.
 */
export interface PlanParticipant {
  plan_id: string;
  user_id: string;
  role: 'patient' | 'contact' | string;
  response: 'accepted' | 'declined' | string | null;
  responded_activity: string | null;
  responded_starts_at: string | null;
  response_at: string | null;
  simulated: boolean;
  display_name?: string | null;
}

export interface PlanWithParticipants extends Plan {
  participants: PlanParticipant[];
}

export interface AgentEvent {
  id: string;
  seq: number;
  event_type: EventType | string;
  safe_summary: string;
  session_id: string | null;
  plan_id: string | null;
  call_id: string | null;
  payload: Record<string, unknown> | null;
  simulated: boolean;
  timestamp: string;
}

// ---------------------------------------------------------------------------
// Core fetch helpers
// ---------------------------------------------------------------------------

async function parseJsonSafely(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!text) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

function isErrorEnvelope(body: unknown): body is ApiErrorEnvelope {
  return (
    typeof body === 'object' &&
    body !== null &&
    'error' in body &&
    typeof (body as ApiErrorEnvelope).error === 'object'
  );
}

export interface RequestOptions {
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

async function request<T>(
  method: 'GET' | 'POST' | 'PATCH' | 'DELETE',
  path: string,
  body?: unknown,
  options?: RequestOptions,
): Promise<T> {
  const headers: Record<string, string> = { ...(options?.headers ?? {}) };
  let payload: BodyInit | undefined;
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  }

  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers,
      body: payload,
      signal: options?.signal,
    });
  } catch (err) {
    throw new ApiError(
      'provider_error',
      err instanceof Error ? err.message : 'Network request failed',
      0,
    );
  }

  const parsed = await parseJsonSafely(res);

  if (!res.ok) {
    if (isErrorEnvelope(parsed)) {
      throw new ApiError(parsed.error.code, parsed.error.message, res.status);
    }
    throw new ApiError(
      'provider_error',
      `Request failed with status ${res.status}`,
      res.status,
    );
  }

  if (isErrorEnvelope(parsed)) {
    // Some backends return 200 with an error envelope; treat as an error anyway.
    throw new ApiError(parsed.error.code, parsed.error.message, res.status);
  }

  return parsed as T;
}

function qs(params: Record<string, string | number | undefined>): string {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined);
  if (entries.length === 0) return '';
  const search = new URLSearchParams();
  for (const [k, v] of entries) search.set(k, String(v));
  return `?${search.toString()}`;
}

// ---------------------------------------------------------------------------
// 4.1 Public / onboarding
// ---------------------------------------------------------------------------

export function getHealth(): Promise<HealthResponse> {
  return request('GET', '/health');
}

export function getStatus(): Promise<StatusResponse> {
  return request('GET', '/api/status');
}

export function qrPngUrl(target?: string): string {
  return `/api/qr.png${qs({ target })}`;
}

export function signup(body: SignupRequest): Promise<SignupResponse> {
  return request('POST', '/api/signup', body);
}

export function verify(body: VerifyRequest): Promise<VerifyResponse> {
  return request('POST', '/api/verify', body);
}

export function postOnboardingPreferences(
  body: OnboardingPreferencesRequest,
): Promise<OkResponse> {
  return request('POST', '/api/onboarding/preferences', body);
}

export function activateOutreach(
  body: OutreachActivateRequest,
): Promise<OutreachActivateResponse> {
  return request('POST', '/api/outreach/activate', body);
}

// ---------------------------------------------------------------------------
// 4.2 Circle & dual consent
// ---------------------------------------------------------------------------

export function createCircleInvite(
  body: CircleInviteRequest,
): Promise<CircleInviteResponse> {
  return request('POST', '/api/circle/invite', body);
}

export function getInvite(token: string): Promise<InviteDetails> {
  return request('GET', `/api/invite/${encodeURIComponent(token)}`);
}

export function acceptInvite(
  token: string,
  body: InviteAcceptRequest = {},
): Promise<InviteAcceptResponse> {
  return request('POST', `/api/invite/${encodeURIComponent(token)}/accept`, body);
}

export function declineInvite(token: string): Promise<InviteDeclineResponse> {
  return request('POST', `/api/invite/${encodeURIComponent(token)}/decline`);
}

export function getConsents(patientId: string): Promise<Consent[]> {
  return request('GET', `/api/consents${qs({ patient_id: patientId })}`);
}

export function createConsent(
  body: CreateConsentRequest,
): Promise<CreateConsentResponse> {
  return request('POST', '/api/consents', body);
}

export function revokeConsent(
  body: RevokeConsentRequest,
): Promise<RevokeConsentResponse> {
  return request('POST', '/api/consents/revoke', body);
}

export function smsStop(body: SmsStopRequest): Promise<SmsStopResponse> {
  return request('POST', '/api/sms/stop', body);
}

// ---------------------------------------------------------------------------
// 4.3 Companion & memory
// ---------------------------------------------------------------------------

export function postCompanionMessage(
  body: CompanionMessageRequest,
): Promise<CompanionMessageResponse> {
  return request('POST', '/api/companion/message', body);
}

export function getMemories(
  userId: string,
  audience: MemoryAudience = 'owner',
): Promise<Memory[]> {
  return request('GET', `/api/memories${qs({ user_id: userId, audience })}`);
}

export function patchMemory(
  id: string,
  body: PatchMemoryRequest,
): Promise<Memory> {
  return request('PATCH', `/api/memories/${encodeURIComponent(id)}`, body);
}

export function deleteMemory(id: string): Promise<DeleteMemoryResponse> {
  return request('DELETE', `/api/memories/${encodeURIComponent(id)}`);
}

// ---------------------------------------------------------------------------
// 4.4 Plans, demo, events
// ---------------------------------------------------------------------------

export interface DashboardOptions {
  dashboardToken: string;
}

function dashboardHeaders(opts: DashboardOptions): Record<string, string> {
  return { 'X-Dashboard-Token': opts.dashboardToken };
}

export function demoReset(opts: DashboardOptions): Promise<DemoResetResponse> {
  return request('POST', '/api/demo/reset', undefined, {
    headers: dashboardHeaders(opts),
  });
}

export function demoStart(
  body: DemoStartRequest,
  opts: DashboardOptions,
): Promise<DemoStartResponse> {
  return request('POST', '/api/demo/start', body, {
    headers: dashboardHeaders(opts),
  });
}

export function demoSamResponse(
  body: DemoSamResponseRequest,
  opts: DashboardOptions,
): Promise<DemoSamResponseResponse> {
  return request('POST', '/api/demo/sam-response', body, {
    headers: dashboardHeaders(opts),
  });
}

export function getPlan(
  id: string,
  opts: DashboardOptions,
): Promise<PlanWithParticipants> {
  return request('GET', `/api/plans/${encodeURIComponent(id)}`, undefined, {
    headers: dashboardHeaders(opts),
  });
}

export function getRecentEvents(
  limit = 200,
  opts?: DashboardOptions,
): Promise<AgentEvent[]> {
  return request('GET', `/api/events/recent${qs({ limit })}`, undefined, {
    headers: opts ? dashboardHeaders(opts) : undefined,
  });
}

export function eventsStreamUrl(): string {
  return '/api/events/stream';
}

// ---------------------------------------------------------------------------
// Judge pitch scenario (fictional Josh + three independently consented friends)
// ---------------------------------------------------------------------------

export interface PitchCall {
  id: string;
  contact_id: string;
  display_name: string;
  status: string;
  mode: VoiceMode;
  purpose: string;
  requested_at: string;
  ended_at: string | null;
}

export interface PitchAvailability {
  user_id: string;
  display_name: string;
  start_at: string;
  end_at: string;
  source: string;
}

export interface PitchStatus {
  people: Array<{ id: string; display_name: string; role: string }>;
  plan: PlanWithParticipants | null;
  calls: PitchCall[];
  availability: PitchAvailability[];
  baseline: {
    current_gap_days: number | null;
    median_gap_days: number | null;
    deviation_ratio: number | null;
    trend: string;
    events_considered: number;
    basis: string;
  } | null;
  approved_memories: Array<{
    value: string;
    source_quote: string;
    confidence: number;
    confirmed: number;
    visibility: string;
    created_at: string;
  }>;
  interaction_history: Array<{ label: string; count: number }>;
  positioning: string;
  stats: {
    recorded_interactions: number;
    approved_memories: number;
    availability_windows: number;
    real_calls: number;
  };
}

export function pitchOnboardingCall(): Promise<{ call_id: string; status: string; mode: VoiceMode; duplicate?: boolean }> {
  return request('POST', '/api/pitch/onboarding-call');
}

export function pitchReset(): Promise<Record<string, unknown>> {
  return request('POST', '/api/pitch/reset');
}

export function pitchStart(forceMode?: VoiceMode): Promise<{ plan_id: string; call_id: string; mode: VoiceMode }> {
  return request('POST', '/api/pitch/start', forceMode ? { force_mode: forceMode } : {});
}

export function pitchRehearse(): Promise<{ plan_id: string; state: string; simulated: true }> {
  return request('POST', '/api/pitch/rehearse');
}

export function pitchSimulateThreeWeeks(): Promise<Record<string, unknown>> {
  return request('POST', '/api/pitch/simulate-three-weeks');
}

export function pitchJoshResponse(planId: string, response: 'accepted' | 'declined'): Promise<Record<string, unknown>> {
  return request('POST', '/api/pitch/josh-response', { plan_id: planId, response });
}

export function getPitchStatus(): Promise<PitchStatus> {
  return request('GET', '/api/pitch/status');
}
