import { useEffect, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import {
  ApiError,
  deleteMemory,
  getMemories,
  patchMemory,
  postCompanionMessage,
  type Memory,
  type ReasoningMode,
  type VisibilityLevel,
} from '../api/client';
import { Button, Card, PageHeader, Textarea } from '../components/ui';
import { ErrorNotice } from '../components/ErrorNotice';
import { ReasoningModeBadge, VisibilityBadge } from '../components/Badges';
import { useLocalStorage } from '../hooks/useLocalStorage';

interface ChatTurn {
  role: 'user' | 'assistant';
  text: string;
  reasoningMode?: ReasoningMode;
}

const VISIBILITY_OPTIONS: VisibilityLevel[] = [
  'private_to_sam',
  'agent_internal',
  'shareable_with_contact',
  'public_demo_safe',
];

export function Companion() {
  const [userId] = useLocalStorage('stillhere_user_id');
  const [sessionId, setSessionId] = useLocalStorage('stillhere_session_id');

  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);

  const [memories, setMemories] = useState<Memory[]>([]);
  const [memoriesError, setMemoriesError] = useState<string | null>(null);
  const [memoriesLoading, setMemoriesLoading] = useState(true);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState('');
  const [editVisibility, setEditVisibility] = useState<VisibilityLevel>('private_to_sam');
  const [memoryActionError, setMemoryActionError] = useState<string | null>(null);

  async function loadMemories() {
    if (!userId) return;
    setMemoriesLoading(true);
    setMemoriesError(null);
    try {
      const res = await getMemories(userId, 'owner');
      setMemories(res);
    } catch (err) {
      setMemoriesError(err instanceof ApiError ? err.message : 'Could not load memories.');
    } finally {
      setMemoriesLoading(false);
    }
  }

  useEffect(() => {
    loadMemories();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId]);

  async function handleSend(e: FormEvent) {
    e.preventDefault();
    if (!userId || !draft.trim()) return;
    const text = draft.trim();
    setTurns((prev) => [...prev, { role: 'user', text }]);
    setDraft('');
    setSending(true);
    setSendError(null);
    try {
      const res = await postCompanionMessage({
        user_id: userId,
        text,
        session_id: sessionId || undefined,
      });
      setSessionId(res.session_id);
      setTurns((prev) => [
        ...prev,
        { role: 'assistant', text: res.reply, reasoningMode: res.reasoning_mode },
      ]);
      if (res.memories_extracted.length > 0) {
        await loadMemories();
      }
    } catch (err) {
      setSendError(err instanceof ApiError ? err.message : 'The companion could not respond.');
    } finally {
      setSending(false);
    }
  }

  function startEdit(memory: Memory) {
    setEditingId(memory.id);
    setEditValue(memory.value);
    setEditVisibility(memory.visibility);
    setMemoryActionError(null);
  }

  async function saveEdit() {
    if (!editingId) return;
    try {
      await patchMemory(editingId, { value: editValue, visibility: editVisibility });
      setEditingId(null);
      await loadMemories();
    } catch (err) {
      setMemoryActionError(err instanceof ApiError ? err.message : 'Could not save that edit.');
    }
  }

  async function handleDelete(id: string) {
    try {
      await deleteMemory(id);
      await loadMemories();
    } catch (err) {
      setMemoryActionError(err instanceof ApiError ? err.message : 'Could not delete that memory.');
    }
  }

  if (!userId) {
    return (
      <div className="mx-auto max-w-lg">
        <PageHeader title="Companion" />
        <Card>
          <p className="text-sm" style={{ color: 'var(--sh-text-muted)' }}>
            We don't have a signed-up account in this browser yet.{' '}
            <Link to="/signup" className="font-semibold" style={{ color: 'var(--sh-accent)' }}>
              Sign up first
            </Link>
            .
          </p>
        </Card>
      </div>
    );
  }

  return (
    <div className="grid gap-8 md:grid-cols-[1.3fr_1fr]">
      <div>
        <PageHeader title="Companion" subtitle="A space to talk, whenever you want." />
        <Card className="flex h-[32rem] flex-col">
          <div className="flex-1 space-y-4 overflow-y-auto pr-1">
            {turns.length === 0 && (
              <p className="text-sm" style={{ color: 'var(--sh-text-muted)' }}>
                Say hello to get started.
              </p>
            )}
            {turns.map((turn, idx) => (
              <div
                key={idx}
                className={`max-w-[85%] rounded-2xl px-4 py-2.5 text-sm ${
                  turn.role === 'user' ? 'ml-auto text-white' : ''
                }`}
                style={
                  turn.role === 'user'
                    ? { background: 'var(--sh-accent)' }
                    : { background: 'var(--sh-accent-soft)' }
                }
              >
                <p className="whitespace-pre-wrap">{turn.text}</p>
                {turn.role === 'assistant' && turn.reasoningMode && (
                  <div className="mt-2">
                    <ReasoningModeBadge mode={turn.reasoningMode} />
                  </div>
                )}
              </div>
            ))}
          </div>
          <ErrorNotice message={sendError} />
          <form className="mt-4 flex gap-3" onSubmit={handleSend}>
            <Textarea
              rows={2}
              placeholder="Type a message…"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  handleSend(e as unknown as FormEvent);
                }
              }}
            />
            <Button type="submit" variant="primary" disabled={sending || !draft.trim()}>
              {sending ? 'Sending…' : 'Send'}
            </Button>
          </form>
        </Card>
      </div>

      <div>
        <h2 className="mb-4 mt-[4.25rem] text-lg font-semibold">Memory panel</h2>
        <Card className="max-h-[32rem] overflow-y-auto">
          <ErrorNotice message={memoriesError} />
          <ErrorNotice message={memoryActionError} />
          {memoriesLoading ? (
            <p className="text-sm" style={{ color: 'var(--sh-text-muted)' }}>
              Loading memories…
            </p>
          ) : memories.length === 0 ? (
            <p className="text-sm" style={{ color: 'var(--sh-text-muted)' }}>
              Nothing remembered yet. As you talk, things worth remembering will show up here.
            </p>
          ) : (
            <ul className="space-y-4">
              {memories.map((memory) => (
                <li key={memory.id} className="rounded-xl border p-4 text-sm" style={{ borderColor: 'var(--sh-border)' }}>
                  {editingId === memory.id ? (
                    <div className="space-y-2">
                      <Textarea
                        rows={2}
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                      />
                      <select
                        className="w-full rounded-xl border bg-transparent px-3 py-2 text-sm"
                        style={{ borderColor: 'var(--sh-border)' }}
                        value={editVisibility}
                        onChange={(e) => setEditVisibility(e.target.value as VisibilityLevel)}
                      >
                        {VISIBILITY_OPTIONS.map((v) => (
                          <option key={v} value={v}>
                            {v}
                          </option>
                        ))}
                      </select>
                      <div className="flex gap-2">
                        <Button type="button" variant="primary" onClick={saveEdit}>
                          Save
                        </Button>
                        <Button type="button" variant="secondary" onClick={() => setEditingId(null)}>
                          Cancel
                        </Button>
                      </div>
                    </div>
                  ) : (
                    <>
                      <div className="flex items-start justify-between gap-2">
                        <p className="font-medium">{memory.value}</p>
                        <VisibilityBadge visibility={memory.visibility} />
                      </div>
                      <p className="mt-1 text-xs uppercase tracking-wide" style={{ color: 'var(--sh-text-muted)' }}>
                        {memory.kind} · confidence {(memory.confidence * 100).toFixed(0)}%
                      </p>
                      {memory.source_quote && (
                        <p className="mt-2 border-l-2 pl-3 text-xs italic" style={{ borderColor: 'var(--sh-accent)', color: 'var(--sh-text-muted)' }}>
                          “{memory.source_quote}”
                        </p>
                      )}
                      <div className="mt-3 flex gap-2">
                        <Button type="button" variant="secondary" onClick={() => startEdit(memory)}>
                          Edit
                        </Button>
                        <Button type="button" variant="danger" onClick={() => handleDelete(memory.id)}>
                          Delete
                        </Button>
                      </div>
                    </>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
